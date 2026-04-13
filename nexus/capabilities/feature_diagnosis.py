"""
Per-feature diagnosis — scoped Tier 1 investigation + markdown report.

diagnose_feature(feature_id) runs the existing investigation pipeline
with a question composed from the feature registry, then builds a
downloadable markdown report combining:

  - current feature health signals
  - the synthesizer's diagnosis (root cause + explanation + actions)
  - evidence gaps
  - sources checked

Never raises — on error, the report carries the error text.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def diagnose_feature(feature_id: str) -> dict[str, Any]:
    """Run a scoped Tier 1 investigation + return a downloadable report."""
    from nexus.capabilities.feature_health import FEATURES, get_all_feature_health
    from nexus.capabilities.investigation import investigate

    fdef = FEATURES.get(feature_id)
    if not fdef:
        return {"error": f"Unknown feature: {feature_id}"}

    question = (
        f"Diagnose the {fdef['name']} feature. "
        f"It covers: {fdef['description']}. "
        f"Check synthetic tests, health signals, and recent errors. "
        f"Focus on checks: {', '.join(fdef.get('health_checks', []) or ['(none)'])}."
    )

    # Run the investigation — this hits the 6 gatherers + Bedrock synth.
    try:
        invest = await investigate(question, timeframe_minutes=60)
    except Exception as exc:
        logger.warning("investigation raised for %s: %s", feature_id, exc)
        invest = {"diagnosis": {}, "evidence": {}, "sources_returned": [],
                  "duration_seconds": 0, "error": str(exc)[:200]}

    # Gather current feature health for the report header.
    try:
        health = await get_all_feature_health()
        feature_health = health.get("features", {}).get(feature_id, {})
    except Exception:
        logger.debug("feature health snapshot failed", exc_info=True)
        feature_health = {}

    report_md = _build_report(feature_id, fdef, feature_health, invest)

    return {
        "feature_id": feature_id,
        "feature_name": fdef["name"],
        "health": feature_health,
        "diagnosis": invest.get("diagnosis", {}),
        "evidence": invest.get("evidence", {}),
        "report_markdown": report_md,
        "duration_seconds": invest.get("duration_seconds", 0),
        "timestamp": _now_iso(),
    }


def _build_report(fid: str, fdef: dict[str, Any],
                   health: dict[str, Any], invest: dict[str, Any]) -> str:
    """Render the downloadable markdown."""
    diag = invest.get("diagnosis", {}) or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# {fdef['name']} — Feature Diagnosis Report",
        f"Generated: {now}",
        "",
        "## Status",
        f"- Health: **{(health.get('status') or 'unknown').upper()}**",
        f"- Errors: {health.get('errors', 0)}",
        f"- Warnings: {health.get('warnings', 0)}",
        f"- Status line: {health.get('status_line', 'N/A')}",
        "",
        "## Diagnosis",
        f"- Confidence: {diag.get('confidence', 0)}%",
        f"- Severity: {diag.get('severity', 'unknown')}",
        f"- Root cause: {diag.get('root_cause') or '(no root cause identified)'}",
        "",
        "### Explanation",
        diag.get("explanation") or "(no explanation)",
        "",
        "## Recommended Actions",
    ]

    actions = diag.get("recommended_actions") or []
    if actions:
        for a in actions:
            if isinstance(a, dict):
                lines.append(
                    f"- [{a.get('priority', '?')}] ({a.get('type', '?')}) "
                    f"{a.get('action', '')}"
                )
            else:
                lines.append(f"- {a}")
    else:
        lines.append("- (none provided)")

    lines += ["", "## Feature Signals"]
    for e in health.get("error_details", []) or []:
        lines.append(f"- ❌ {e}")
    for w in health.get("warning_details", []) or []:
        lines.append(f"- ⚠ {w}")
    if not (health.get("error_details") or health.get("warning_details")):
        lines.append("- (no issues detected by local checks)")

    lines += [
        "",
        "## Investigation",
        f"- Sources checked: {', '.join(invest.get('sources_returned', []) or []) or '(none)'}",
        f"- Duration: {invest.get('duration_seconds', 0)}s",
    ]
    if invest.get("error"):
        lines.append(f"- Investigation error: {invest['error']}")

    lines += ["", "## Evidence Gaps"]
    gaps = diag.get("evidence_gaps") or []
    if gaps:
        for g in gaps:
            lines.append(f"- {g}")
    else:
        lines.append("- (none reported)")

    lines += [
        "",
        "---",
        f"Paste into Claude with: \"Here is the {fdef['name']} diagnosis report. "
        "What should we fix?\"",
    ]
    return "\n".join(lines)
