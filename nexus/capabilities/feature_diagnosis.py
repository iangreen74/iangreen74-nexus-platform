"""
Async multi-phase diagnosis with auto-tier escalation.

Three levels share one pipeline:
  - feature : FEATURES[feature_id] (projects, aria_chat, …)
  - tenant  : a Tenant node in Neptune
  - goal    : platform-wide metrics (SFS rate, PRs, etc.)

Three phases run in order. Each phase decides whether the next runs:
  Phase 1 — quick_check      (5–10s)  synthetics + health signals
  Phase 2 — deep_analysis    (30–60s) reuses investigate() from investigation.py
  Phase 3 — agent_investigation (5–30m) Step Function spawn

Escalation:
  Phase 2 runs when phase1 finds any issue OR confidence < 80.
  Phase 3 runs when phase2 confidence < 60.

start_diagnosis(...) returns a job_id immediately. get_diagnosis(job_id)
polls. The store is in-process (lost on restart) — acceptable for now;
persist to Neptune if durability matters.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


_active_diagnoses: dict[str, dict[str, Any]] = {}
VALID_LEVELS = {"feature", "tenant", "goal"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def start_diagnosis(target_id: str, level: str = "feature") -> dict[str, Any]:
    """Kick off an async diagnosis. Returns the initial job record."""
    if level not in VALID_LEVELS:
        return {"error": f"Invalid level '{level}'; expected one of {VALID_LEVELS}"}
    if level == "feature":
        from nexus.capabilities.feature_health import FEATURES

        if target_id not in FEATURES:
            return {"error": f"Unknown feature: {target_id}"}

    job_id = f"diag-{uuid.uuid4().hex[:10]}"
    record = {
        "job_id": job_id,
        "target_id": target_id,
        "level": level,
        "status": "starting",
        "phase": "quick_check",
        "phase_label": "Phase 1: Quick check — synthetics + health signals",
        "started_at": _now_iso(),
        "phases_completed": [],
        "confidence": 0,
        "report": None,
    }
    _active_diagnoses[job_id] = record
    asyncio.create_task(_run_diagnosis(job_id))
    return record


async def get_diagnosis(job_id: str) -> dict[str, Any]:
    rec = _active_diagnoses.get(job_id)
    return rec if rec else {"error": "job not found"}


async def _run_diagnosis(job_id: str) -> None:
    rec = _active_diagnoses[job_id]
    target_id = rec["target_id"]
    level = rec["level"]
    sections: list[dict[str, Any]] = []

    try:
        rec["status"] = "running"

        p1 = await _phase1_quick_check(target_id, level)
        sections.append(p1)
        rec["phases_completed"].append({
            "phase": "quick_check", "duration_s": p1["duration"],
            "findings": len(p1.get("findings", [])),
        })
        rec["confidence"] = p1["confidence"]

        if p1["confidence"] < 80 or p1.get("findings"):
            rec["phase"] = "deep_analysis"
            rec["phase_label"] = "Phase 2: Deep analysis — investigation pipeline (Bedrock)"
            p2 = await _phase2_deep_analysis(target_id, level, p1)
            sections.append(p2)
            rec["phases_completed"].append({
                "phase": "deep_analysis", "duration_s": p2["duration"],
                "findings": len(p2.get("findings", [])),
            })
            rec["confidence"] = max(rec["confidence"], p2["confidence"])

            if p2["confidence"] < 60:
                rec["phase"] = "agent_investigation"
                rec["phase_label"] = "Phase 3: Agent investigation — Step Function spawn"
                p3 = await _phase3_agent(target_id, level, p1, p2)
                sections.append(p3)
                rec["phases_completed"].append({
                    "phase": "agent_investigation", "duration_s": p3["duration"],
                })
                rec["confidence"] = max(rec["confidence"], p3["confidence"])

        rec["report"] = _build_report(target_id, level, sections, rec)
        rec["status"] = "complete"
        rec["phase"] = "done"
        rec["phase_label"] = "Complete"
        rec["completed_at"] = _now_iso()
    except Exception as exc:
        logger.exception("diagnosis %s failed", job_id)
        rec["status"] = "failed"
        rec["phase_label"] = f"Failed: {type(exc).__name__}: {str(exc)[:120]}"
        rec["report"] = _build_failure_report(target_id, level, sections, str(exc))
        rec["completed_at"] = _now_iso()


async def _phase1_quick_check(target_id: str, level: str) -> dict[str, Any]:
    start = time.time()
    findings: list[str] = []
    confidence = 90

    try:
        if level == "feature":
            from nexus.capabilities.feature_health import (
                FEATURES, _evaluate_feature, _synthetic_results_by_name,
            )
            synthetics = await asyncio.to_thread(_synthetic_results_by_name)
            fdef = FEATURES.get(target_id, {})
            health = _evaluate_feature(target_id, fdef, synthetics)
            findings.extend(health.get("error_details", []) or [])
            findings.extend(health.get("warning_details", []) or [])
            confidence = 90 if not findings else 50

        elif level == "tenant":
            findings.extend(await asyncio.to_thread(_tenant_quick_checks, target_id))
            confidence = 80 if not findings else 40

        elif level == "goal":
            findings.extend(await asyncio.to_thread(_goal_quick_checks))
            confidence = 85 if not findings else 50
    except Exception as exc:
        findings.append(f"Phase 1 error: {type(exc).__name__}: {str(exc)[:120]}")
        confidence = 40

    return {
        "phase": "quick_check",
        "findings": findings,
        "confidence": confidence,
        "duration": round(time.time() - start, 1),
        "summary": f"Phase 1: {len(findings)} finding(s), {confidence}% confidence",
    }


def _tenant_quick_checks(tid: str) -> list[str]:
    from nexus import neptune_client

    out: list[str] = []
    rows = neptune_client.query(
        "MATCH (t:Tenant {tenant_id: $tid}) "
        "RETURN t.mission_stage AS stage, t.repo_url AS repo_url",
        {"tid": tid},
    )
    if not rows:
        return [f"Tenant {tid[:12]} not found"]
    r = rows[0]
    stage = r.get("stage")
    if not stage or stage in ("None", "unknown"):
        out.append(f"Tenant has no mission_stage (current: {stage!r})")
    if not r.get("repo_url"):
        out.append("Tenant.repo_url is empty")
    pending = neptune_client.query(
        "MATCH (m:MissionTask {tenant_id: $tid, status: 'pending'}) RETURN count(m) AS c",
        {"tid": tid},
    )
    cnt = (pending[0].get("c", 0) if pending else 0) or 0
    if cnt > 5:
        out.append(f"{cnt} pending tasks — may be stuck")
    return out


def _goal_quick_checks() -> list[str]:
    from nexus import neptune_client

    out: list[str] = []
    total = neptune_client.query("MATCH (t:Tenant) RETURN count(t) AS c")
    exe = neptune_client.query(
        "MATCH (t:Tenant {mission_stage: 'executing'}) RETURN count(t) AS c"
    )
    total_c = (total[0].get("c", 0) if total else 0) or 0
    exe_c = (exe[0].get("c", 0) if exe else 0) or 0
    if total_c and exe_c == 0:
        out.append(f"No tenants actively executing ({total_c} total)")
    prs = neptune_client.query("MATCH (p:PullRequest) RETURN count(p) AS c")
    if prs and (prs[0].get("c", 0) or 0) == 0:
        out.append("No PullRequest nodes in graph")
    return out


async def _phase2_deep_analysis(target_id: str, level: str,
                                  p1: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    findings_txt = "; ".join(p1.get("findings", []) or ["(none)"])
    if level == "feature":
        from nexus.capabilities.feature_health import FEATURES
        fdef = FEATURES.get(target_id, {})
        question = (
            f"Deep analysis of {fdef.get('name', target_id)} "
            f"({fdef.get('description', '')}). Phase 1 found: {findings_txt}."
        )
    elif level == "tenant":
        question = f"Deep analysis of tenant {target_id}. Phase 1 found: {findings_txt}."
    else:
        question = f"Deep analysis of platform goals. Phase 1 found: {findings_txt}."

    try:
        from nexus.capabilities.investigation import investigate
        result = await investigate(question, timeframe_minutes=60)
    except Exception as exc:
        logger.warning("investigate failed in phase2: %s", exc)
        result = {"diagnosis": {}, "evidence": {}, "error": str(exc)[:200]}

    diag = result.get("diagnosis", {}) or {}
    return {
        "phase": "deep_analysis",
        "findings": diag.get("recommended_actions", []) or [],
        "confidence": diag.get("confidence", 60) or 60,
        "diagnosis": diag,
        "evidence": result.get("evidence", {}),
        "sources": result.get("sources_returned", []),
        "duration": round(time.time() - start, 1),
        "summary": f"Phase 2: {diag.get('confidence', 0)}% confidence",
    }


async def _phase3_agent(target_id: str, level: str,
                         p1: dict[str, Any], p2: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    try:
        import boto3
        sfn = boto3.client("stepfunctions", region_name="us-east-1")
        execution = await asyncio.to_thread(
            sfn.start_execution,
            stateMachineArn="arn:aws:states:us-east-1:418295677815:stateMachine:forgescaler-investigation-v2",
            input=json.dumps({
                "trigger_type": "feature_diagnosis",
                "target_id": target_id,
                "level": level,
                "phase1_findings": p1.get("findings", []),
                "phase2_confidence": p2.get("confidence", 0),
                "requested_at": _now_iso(),
            }),
        )
        return {
            "phase": "agent_investigation",
            "execution_arn": execution.get("executionArn"),
            "confidence": 70,
            "duration": round(time.time() - start, 1),
            "summary": "Phase 3: Step Function spawned",
        }
    except Exception as exc:
        logger.warning("phase3 step function spawn failed: %s", exc)
        return {
            "phase": "agent_investigation",
            "error": str(exc)[:200],
            "confidence": p2.get("confidence", 60),
            "duration": round(time.time() - start, 1),
            "summary": f"Phase 3: could not spawn agent ({type(exc).__name__})",
        }


def _build_report(target_id: str, level: str, sections: list[dict[str, Any]],
                   rec: dict[str, Any]) -> str:
    """Render the downloadable markdown. Never raises."""
    header = {"feature": "Feature", "tenant": "Tenant", "goal": "Goal"}.get(level, level)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# {header} Diagnosis: {target_id}",
        f"Generated: {now}",
        f"Overall confidence: {rec.get('confidence', 0)}%",
        f"Phases completed: {len(rec.get('phases_completed', []))}/3",
        "",
        "## Timeline",
    ]
    for pc in rec.get("phases_completed", []) or []:
        lines.append(
            f"- {pc.get('phase', '?')}: {pc.get('duration_s', '?')}s, "
            f"{pc.get('findings', 0)} finding(s)"
        )
    lines.append("")

    for section in sections:
        title = section.get("phase", "unknown").replace("_", " ").title()
        lines.append(f"## {title}")
        lines.append(
            f"_Duration {section.get('duration', '?')}s · "
            f"Confidence {section.get('confidence', '?')}%_"
        )
        lines.append("")
        for f in section.get("findings", []) or []:
            if isinstance(f, dict):
                lines.append(
                    f"- [{f.get('priority', '?')}] ({f.get('type', '?')}) "
                    f"{f.get('action', '')}"
                )
            else:
                lines.append(f"- {f}")
        diag = section.get("diagnosis") or {}
        if diag.get("root_cause"):
            lines += ["", f"**Root cause:** {diag['root_cause']}"]
        if diag.get("explanation"):
            lines += ["", diag["explanation"]]
        if section.get("execution_arn"):
            lines += ["", f"Step Function execution: `{section['execution_arn']}`"]
        if section.get("error"):
            lines += ["", f"_Phase error: {section['error']}_"]
        lines.append("")

    lines += [
        "---",
        f"Paste into Claude with: \"Here is the {level} diagnosis for "
        f"{target_id}. What should we fix?\"",
    ]
    return "\n".join(lines)


def _build_failure_report(target_id: str, level: str,
                           sections: list[dict[str, Any]], err: str) -> str:
    lines = [
        f"# Diagnosis Failed: {target_id} ({level})",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"**Error:** `{err}`",
        "",
        "## Partial findings",
    ]
    if not sections:
        lines.append("- (no phase completed)")
    for s in sections:
        lines.append(f"- {s.get('summary', s.get('phase', '?'))}")
    return "\n".join(lines)
