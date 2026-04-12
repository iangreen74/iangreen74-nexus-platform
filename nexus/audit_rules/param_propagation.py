"""Rule: functions that accept project_id but callers don't pass it."""
from __future__ import annotations

import os
import re

from nexus.audit_rules.base import AuditRule, Finding

MUST_HAVE_PID = [
    "ingest_repo", "generate", "get_brief", "get_conversation_history",
    "send_message", "execute_next_task", "regenerate_brief",
    "add_entry", "create_task_from_request",
]


class ParamPropagation(AuditRule):
    name = "param_propagation"
    description = "Calls to project-aware functions missing project_id argument"
    severity = "high"

    def scan(self, repo_path: str) -> list[Finding]:
        findings: list[Finding] = []
        for root, _, files in os.walk(repo_path):
            if ".git" in root or "node_modules" in root:
                continue
            for fname in files:
                if not fname.endswith(".py") or "test_" in fname:
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, repo_path)
                try:
                    lines = open(fpath).readlines()
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    for func in MUST_HAVE_PID:
                        pattern = rf"{func}\s*\("
                        if not re.search(pattern, line):
                            continue
                        if line.strip().startswith("def "):
                            continue
                        # Follow multi-line calls
                        call_text = line
                        j = i + 1
                        while j < len(lines) and call_text.count("(") > call_text.count(")"):
                            call_text += lines[j]
                            j += 1
                        if "project_id" in call_text:
                            continue
                        findings.append(Finding(
                            rule=self.name, severity="high",
                            file=rel, line=i + 1,
                            message=f"Call to {func}() without project_id parameter",
                            fix_hint="Add project_id=pid to the call",
                            context=line.strip()[:120]))
        return findings
