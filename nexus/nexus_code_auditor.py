"""
Overwatch Code Auditor — structural analysis of aria-platform.

Clones aria-platform (or scans a local path), runs all audit rules,
compiles a report with health score + per-rule findings. Results are
stored as code_audit events in the Overwatch graph.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.config import AWS_REGION, MODE

logger = logging.getLogger(__name__)

DEFAULT_REPO_URL = "https://github.com/iangreen74/aria-platform.git"


def run_audit(
    repo_url: str | None = None,
    local_path: str | None = None,
    store_results: bool = True,
) -> dict[str, Any]:
    """Run full code audit. Returns structured report (never raises)."""
    from nexus.audit_rules import ALL_RULES

    if local_path and os.path.isdir(os.path.expanduser(local_path)):
        repo_path = os.path.expanduser(local_path)
        cleanup = False
    else:
        repo_path = _clone_repo(repo_url or DEFAULT_REPO_URL)
        if not repo_path:
            return {"status": "error", "error": "Failed to clone repo"}
        cleanup = True

    try:
        all_findings: list[Any] = []
        rule_summaries: list[dict[str, Any]] = []

        for rule_cls in ALL_RULES:
            rule = rule_cls()
            try:
                findings = rule.scan(repo_path)
                all_findings.extend(findings)
                rule_summaries.append({
                    "rule": rule.name,
                    "description": rule.description,
                    "findings": len(findings),
                    "critical": sum(1 for f in findings if f.severity == "critical"),
                    "high": sum(1 for f in findings if f.severity == "high"),
                })
                if findings:
                    logger.info("  %s: %d findings", rule.name, len(findings))
            except Exception as exc:
                logger.exception("Rule %s crashed", rule.name)
                rule_summaries.append({"rule": rule.name, "error": str(exc)[:200]})

        report = {
            "status": "complete",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_findings": len(all_findings),
            "critical": sum(1 for f in all_findings if f.severity == "critical"),
            "high": sum(1 for f in all_findings if f.severity == "high"),
            "medium": sum(1 for f in all_findings if f.severity == "medium"),
            "low": sum(1 for f in all_findings if f.severity == "low"),
            "rules_run": len(rule_summaries),
            "rule_summaries": rule_summaries,
            "findings": [f.to_dict() for f in all_findings],
        }

        deductions = (
            report["critical"] * 10 + report["high"] * 5
            + report["medium"] * 2 + report["low"] * 0.5
        )
        report["health_score"] = max(0, round(100 - deductions))

        if store_results:
            _store_report(report)

        logger.info(
            "Code audit complete: %d findings, health %d/100",
            report["total_findings"], report["health_score"],
        )
        return report
    finally:
        if cleanup and repo_path:
            shutil.rmtree(repo_path, ignore_errors=True)


def get_latest_report() -> dict[str, Any] | None:
    """Get the most recent audit report from Overwatch graph events."""
    try:
        events = overwatch_graph.get_recent_events(limit=200)
        for e in events:
            if e.get("event_type") != "code_audit":
                continue
            details = e.get("details") or {}
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (ValueError, TypeError):
                    continue
            raw = details.get("report")
            if raw:
                try:
                    return json.loads(raw)
                except (ValueError, TypeError):
                    continue
    except Exception as exc:
        logger.warning("get_latest_report failed: %s", exc)
    return None


def format_report_text(report: dict[str, Any] | None) -> str:
    """Format report for the dashboard Copy Report button."""
    if not report or report.get("status") == "error":
        err = (report or {}).get("error", "no data")
        return f"No audit report available — {err}"
    lines = [
        f"CODE HEALTH AUDIT — {report.get('timestamp', '?')[:19]}",
        f"Health Score: {report.get('health_score', '?')}/100",
        f"Findings: {report.get('total_findings', 0)} "
        f"(critical={report.get('critical', 0)}, high={report.get('high', 0)}, "
        f"medium={report.get('medium', 0)}, low={report.get('low', 0)})",
        "",
    ]
    for sev in ("critical", "high", "medium", "low"):
        items = [f for f in report.get("findings", []) if f.get("severity") == sev]
        if not items:
            continue
        lines.append("=" * 60)
        lines.append(f"{sev.upper()} ({len(items)})")
        lines.append("=" * 60)
        for f in items:
            lines.append(
                f"  [{f.get('rule', '?')}] {f.get('file', '?')}:{f.get('line', '?')}"
            )
            lines.append(f"    {f.get('message', '')}")
            if f.get("fix_hint"):
                lines.append(f"    Fix: {f['fix_hint']}")
            lines.append("")
    return "\n".join(lines)


def _clone_repo(url: str) -> str | None:
    """Clone the repo to a temp directory (uses github-token if available)."""
    tmpdir = tempfile.mkdtemp(prefix="audit_")
    clone_url = url
    if MODE == "production":
        try:
            import boto3  # noqa: WPS433

            sm = boto3.client("secretsmanager", region_name=AWS_REGION)
            token = sm.get_secret_value(SecretId="github-token")["SecretString"].strip()
            if token.startswith("{"):
                token = json.loads(token).get("token", token)
            if token and url.startswith("https://"):
                clone_url = url.replace("https://", f"https://{token}@")
        except Exception:
            logger.debug("No github-token; cloning anonymously", exc_info=True)
    try:
        r = subprocess.run(["git", "clone", "--depth", "1", clone_url, tmpdir],
                           capture_output=True, timeout=120)
        if r.returncode != 0:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return None
        return tmpdir
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None


def _store_report(report: dict[str, Any]) -> None:
    """Store audit report as an Overwatch graph event."""
    try:
        overwatch_graph.record_event(
            event_type="code_audit",
            service="aria-platform",
            severity="info" if report.get("critical", 0) == 0 else "warning",
            details={
                "health_score": report.get("health_score", 0),
                "total_findings": report.get("total_findings", 0),
                "critical": report.get("critical", 0),
                "high": report.get("high", 0),
                "report": json.dumps(report),
            },
        )
    except Exception:
        logger.debug("Failed to store audit report", exc_info=True)
