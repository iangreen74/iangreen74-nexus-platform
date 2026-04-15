"""
Agentic Fix Agent — Bedrock-driven counterpart to fix_generator.py.
Given a Finding with file+line, reads the file, asks Bedrock for a
minimal patch, validates, and opens a draft PR via aria_repo. All PRs
are human-approval-gated. Scope is narrow: aria/ and forgescaler/ only,
<200-line files, 3 fixes/hr, Bedrock output must compile and stay <200.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any

from nexus.config import AWS_REGION, OPS_CHAT_MODEL_ID
from nexus.findings import Finding
from nexus.forge.aria_repo import FileChange, create_fix_pr, read_file
from nexus.overwatch_graph import record_fix_attempt

logger = logging.getLogger("nexus.forge.fix_agent")

SUPPORTED_CATEGORIES = {"code_fix", "data_fix"}
SCOPE_PREFIXES = ("aria/", "forgescaler/")
MAX_FILE_LINES = 200
MAX_FIXES_PER_HOUR = 3
_recent_fixes: deque[float] = deque()


def _rate_limited() -> bool:
    now = time.time()
    cutoff = now - 3600
    while _recent_fixes and _recent_fixes[0] < cutoff:
        _recent_fixes.popleft()
    return len(_recent_fixes) >= MAX_FIXES_PER_HOUR


def _mark_fix():
    _recent_fixes.append(time.time())


def _in_scope(path: str) -> bool:
    return any(path.startswith(p) for p in SCOPE_PREFIXES)


def _build_prompt(content: str, path: str, summary: str, line: int) -> str:
    return (
        f"You are fixing a bug in a Python file in the aria-platform repo.\n\n"
        f"File: {path}\nError line: {line}\nFinding: {summary}\n\n"
        f"Here is the current file:\n```python\n{content}\n```\n\n"
        f"Rules:\n"
        f"- Make the MINIMUM change needed to fix the finding.\n"
        f"- Do not refactor unrelated code.\n"
        f"- The file must remain under 200 lines.\n"
        f"- Return ONLY a JSON object with a single key `fixed_file` whose "
        f"value is the full fixed file contents as a string. No prose.\n"
    )


def _invoke_bedrock(prompt: str, max_tokens: int = 4000) -> str:
    """Synchronous Bedrock call. Mirrors investigation._invoke_bedrock."""
    import boto3
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    resp = client.invoke_model(
        modelId=OPS_CHAT_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    body = json.loads(resp["body"].read())
    for block in body.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _parse_fixed_content(raw: str) -> str | None:
    from nexus.capabilities.bedrock_utils import parse_bedrock_json
    parsed = parse_bedrock_json(raw)
    fixed = parsed.get("fixed_file") if isinstance(parsed, dict) else None
    if isinstance(fixed, str) and fixed.strip():
        return fixed
    return None


def _validate_fixed_content(original: str, fixed: str) -> tuple[bool, str]:
    if not fixed or fixed == original:
        return False, "no_change"
    if len(fixed.splitlines()) > MAX_FILE_LINES:
        return False, "exceeds_line_limit"
    try:
        compile(fixed, "<fix_agent>", "exec")
    except SyntaxError as exc:
        return False, f"syntax_error: {exc.msg}"
    return True, "ok"


class FixAgent:
    """Diagnosis-driven Bedrock fix proposer. Opens draft PRs only."""

    def __init__(self, invoker=_invoke_bedrock, reader=read_file, pr_opener=create_fix_pr):
        # Injectable seams keep this unit-testable without mocking boto3.
        self._invoke = invoker
        self._read = reader
        self._open_pr = pr_opener

    def can_fix(self, finding: Finding) -> tuple[bool, str]:
        if not finding.file or not finding.line:
            return False, "missing_file_or_line"
        if finding.category not in SUPPORTED_CATEGORIES:
            return False, f"unsupported_category:{finding.category}"
        if not _in_scope(finding.file):
            return False, "out_of_scope"
        return True, "ok"

    def propose(self, finding: Finding) -> dict[str, Any]:
        ok, reason = self.can_fix(finding)
        if not ok:
            return self._record_and_return(finding, "skipped", reason=reason)

        if _rate_limited():
            return self._record_and_return(finding, "rate_limited", reason="hourly_cap")

        original = self._read(finding.file) or ""
        if not original.strip():
            return self._record_and_return(finding, "skipped", reason="empty_file")
        if len(original.splitlines()) > MAX_FILE_LINES:
            return self._record_and_return(finding, "skipped", reason="source_too_long")

        prompt = _build_prompt(original, finding.file, finding.summary, finding.line)
        try:
            raw = self._invoke(prompt)
        except Exception as exc:
            logger.exception("bedrock invoke failed")
            return self._record_and_return(finding, "failed", reason=f"bedrock:{exc}")

        fixed = _parse_fixed_content(raw)
        if not fixed:
            return self._record_and_return(finding, "no_fix", reason="unparseable_response")

        valid, why = _validate_fixed_content(original, fixed)
        if not valid:
            return self._record_and_return(finding, "rejected", reason=why)

        branch = f"overwatch/fix-{finding.fingerprint()}"
        title = f"fix: {finding.summary[:72]}"
        body = self._pr_body(finding)
        pr = self._open_pr(
            branch_name=branch,
            file_changes=[FileChange(path=finding.file, new_content=fixed, old_content=original)],
            title=title,
            body=body,
        )
        if pr.get("error"):
            return self._record_and_return(finding, "failed", reason=pr["error"])

        _mark_fix()
        return self._record_and_return(
            finding,
            "pr_opened",
            pr_number=pr.get("number"),
            pr_url=pr.get("url"),
        )

    def _pr_body(self, finding: Finding) -> str:
        return (
            f"## Overwatch Fix Agent\n\n"
            f"**Finding:** {finding.summary}\n"
            f"**File:** `{finding.file}` line {finding.line}\n"
            f"**Category:** {finding.category}\n"
            f"**Severity:** {finding.severity}\n\n"
            f"Generated by Bedrock ({OPS_CHAT_MODEL_ID}) from finding fingerprint "
            f"`{finding.fingerprint()}`. Draft PR — human review required before merge."
        )

    @staticmethod
    def _record_and_return(finding: Finding, status: str, **extra: Any) -> dict[str, Any]:
        try:
            record_fix_attempt(
                finding_fingerprint=finding.fingerprint(),
                file_path=finding.file or "",
                category=finding.category,
                status=status,
                pr_number=extra.get("pr_number"),
                pr_url=extra.get("pr_url"),
                reason=extra.get("reason"),
            )
        except Exception:
            logger.exception("record_fix_attempt failed; continuing")
        return {"status": status, **extra}
