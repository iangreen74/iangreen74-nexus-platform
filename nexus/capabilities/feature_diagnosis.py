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
# FIFO queue of job records waiting for the currently running diagnosis
# to finish. Each entry is the same record already stored in
# _active_diagnoses (shared identity, not a copy) so the operator can
# poll its job_id and watch "queued" transition into "starting" →
# "running" → "complete" without a second lookup path.
_diagnosis_queue: list[dict[str, Any]] = []
VALID_LEVELS = {"feature", "tenant", "goal"}

# Safety valve: if a diagnosis coroutine crashes before updating its
# status, the job sits in "running" forever and blocks new requests.
# Anything older than this is treated as dead.
DIAGNOSIS_MAX_RUN_SEC = 300.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def start_diagnosis(target_id: str, level: str = "feature") -> dict[str, Any]:
    """Kick off an async diagnosis. Returns the initial job record.

    Concurrency-gated: only one diagnosis runs at a time across the whole
    task. A second request while one is active returns the active job's
    record with status='busy' rather than spawning another investigation
    pipeline — running two Bedrock+parallel-gatherer jobs on one ECS task
    causes /api/* to 503 for ~60s.
    """
    if level not in VALID_LEVELS:
        return {"error": f"Invalid level '{level}'; expected one of {VALID_LEVELS}"}
    if level == "feature":
        from nexus.capabilities.feature_health import FEATURES

        if target_id not in FEATURES:
            return {"error": f"Unknown feature: {target_id}"}

    # One at a time. Source of truth is the job store itself — no separate
    # lock flag means no risk of a stale lock from a crashed coroutine.
    # Jobs older than DIAGNOSIS_MAX_RUN_SEC are treated as dead so a silent
    # crash can't permanently block the endpoint.
    now_ts = time.time()
    running: dict[str, Any] | None = None
    for existing in list(_active_diagnoses.values()):
        if existing.get("status") not in ("starting", "running"):
            continue
        if now_ts - existing.get("_start_ts", 0) > DIAGNOSIS_MAX_RUN_SEC:
            existing["status"] = "timeout"
            existing["phase_label"] = (
                f"Timed out after {int(DIAGNOSIS_MAX_RUN_SEC)}s — job abandoned"
            )
            existing["completed_at"] = _now_iso()
            logger.warning("reclaiming stuck diagnosis %s (level=%s, target=%s)",
                           existing["job_id"], existing["level"], existing["target_id"])
            continue
        running = existing
        break

    job_id = f"diag-{uuid.uuid4().hex[:10]}"
    record = {
        "job_id": job_id,
        "target_id": target_id,
        "level": level,
        "status": "starting",
        "phase": "quick_check",
        "phase_label": "Phase 1: Quick check — synthetics + health signals",
        "started_at": _now_iso(),
        "_start_ts": now_ts,
        "phases_completed": [],
        "confidence": 0,
        "report": None,
    }
    _active_diagnoses[job_id] = record

    if running is not None:
        # Queue behind the running job. Each queued job keeps its own
        # job_id and will produce its own report once it gets to run.
        record["status"] = "queued"
        record["phase"] = "waiting"
        record["phase_label"] = (
            f"Queued — waiting for {running['level']}/{running['target_id']} to finish "
            f"({len(_diagnosis_queue) + 1} ahead)"
        )
        record["queue_position"] = len(_diagnosis_queue) + 1
        _diagnosis_queue.append(record)
        return record

    asyncio.create_task(_run_diagnosis(job_id))
    return record


def _start_next_queued() -> None:
    """Promote the next queued diagnosis to running. Drops already-
    completed records that snuck in (e.g., cancelled)."""
    while _diagnosis_queue:
        nxt = _diagnosis_queue.pop(0)
        if nxt.get("status") != "queued":
            continue
        nxt["status"] = "starting"
        nxt["phase"] = "quick_check"
        nxt["phase_label"] = "Phase 1: Quick check — synthetics + health signals"
        nxt["_start_ts"] = time.time()
        nxt.pop("queue_position", None)
        asyncio.create_task(_run_diagnosis(nxt["job_id"]))
        return


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
        rec["_start_ts"] = time.time()  # reset TTL clock when actually running

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
    finally:
        # Drain the queue regardless of success/failure so a broken job
        # can't deadlock subsequent diagnoses.
        try:
            _start_next_queued()
        except Exception:
            logger.exception("failed to start next queued diagnosis")


def _synthetic_full_results() -> dict[str, dict[str, Any]]:
    """Return name → full result dict (status, duration_ms, error).

    Relies on synthetic_tests's 60s internal cache — cheap if Phase 1 also
    called _synthetic_results_by_name immediately before.
    """
    try:
        from nexus.synthetic_tests import get_summary
        summary = get_summary() or {}
        out: dict[str, dict[str, Any]] = {}
        for r in summary.get("results", []) or []:
            if isinstance(r, dict) and r.get("name"):
                out[r["name"]] = r
        return out
    except Exception:
        logger.debug("synthetic full results unavailable", exc_info=True)
        return {}


def _enrich_synthetic_findings(findings: list[str],
                                 details: dict[str, dict[str, Any]]) -> list[str]:
    """Append failure detail (error / duration) to bare 'Synthetic X fail' lines."""
    out: list[str] = []
    for f in findings:
        if not isinstance(f, str) or "Synthetic '" not in f:
            out.append(f)
            continue
        try:
            name = f.split("'", 2)[1]
        except (IndexError, ValueError):
            out.append(f)
            continue
        d = details.get(name) or {}
        extra = ""
        if d.get("error"):
            extra = f" — {str(d['error'])[:200]}"
        elif d.get("duration_ms"):
            extra = f" — {d['duration_ms']}ms"
        out.append(f + extra if extra else f)
    return out


async def _phase1_quick_check(target_id: str, level: str) -> dict[str, Any]:
    start = time.time()
    findings: list[str] = []
    confidence = 90

    try:
        if level == "feature":
            from nexus.capabilities.feature_health import (
                FEATURES, _evaluate_feature_async, _synthetic_results_by_name,
            )
            synthetics = await asyncio.to_thread(_synthetic_results_by_name)
            details = await asyncio.to_thread(_synthetic_full_results)
            fdef = FEATURES.get(target_id, {})
            # Async variant applies per-check timeout so a slow Neptune call
            # can't stall Phase 1 indefinitely.
            health = await _evaluate_feature_async(target_id, fdef, synthetics)
            findings.extend(health.get("error_details", []) or [])
            findings.extend(health.get("warning_details", []) or [])
            findings = _enrich_synthetic_findings(findings, details)
            confidence = 90 if not findings else 50

        elif level == "tenant":
            from nexus.capabilities.tenant_checks import tenant_quick_checks
            findings.extend(await asyncio.to_thread(tenant_quick_checks, target_id))
            # Tenant always runs Phase 2 — per-tenant reports are where the
            # operator wants the deepest evidence.
            confidence = 50

        elif level == "goal":
            from nexus.capabilities.goal_checks import goal_quick_checks
            findings.extend(await asyncio.to_thread(goal_quick_checks))
            # Goal always runs Phase 2 — the "God panel" is the comprehensive
            # platform report.
            confidence = 50
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


def _format_evidence(evidence: dict[str, Any]) -> str:
    """Render gathered evidence as human-readable markdown.

    This must always succeed — it's the safety net when Bedrock synthesis
    returns nothing useful. Each known source gets a tailored formatter;
    unknown sources fall through to a JSON dump.
    """
    if not evidence:
        return "No evidence gathered"

    lines: list[str] = []
    for source, data in evidence.items():
        if source.startswith("_"):
            # Surface sentinel keys (e.g. _timeout) so slow gathers are visible.
            if isinstance(data, dict) and data.get("error"):
                lines.append(f"- **{source}**: {str(data['error'])[:200]}")
            continue
        if not isinstance(data, dict):
            lines.append(f"- **{source}**: {str(data)[:200]}")
            continue
        if data.get("error"):
            lines.append(f"- **{source}**: ERROR — {str(data['error'])[:200]}")
            continue

        if source == "ecs":
            services = data.get("services", {}) or {}
            lines.append(f"- **ecs**: {len(services)} services, drift={data.get('drift')}, "
                         f"recommendation={data.get('recommendation', '?')}")
            for svc, info in services.items():
                if isinstance(info, dict):
                    lines.append(f"  - {svc}: image={str(info.get('image', '?'))[:60]}")
        elif source == "neptune":
            lines.append(f"- **neptune**: {data.get('tenant_count', 0)} tenants, "
                         f"{data.get('open_incidents', 0)} open incidents")
            for inc in (data.get("incident_summary") or [])[:3]:
                if isinstance(inc, dict):
                    lines.append(f"  - incident {inc.get('source', '?')}/"
                                 f"{inc.get('type', '?')}: "
                                 f"{str(inc.get('root_cause', ''))[:150]}")
            for t in (data.get("tenants") or [])[:3]:
                if isinstance(t, dict) and (t.get("deploy_stuck") or
                                              t.get("overall_status") not in ("healthy", None)):
                    lines.append(f"  - tenant {str(t.get('id', ''))[:12]}: "
                                 f"stage={t.get('stage')}, status={t.get('overall_status')}, "
                                 f"deploy_stuck={t.get('deploy_stuck')}")
        elif source == "synthetic":
            lines.append(f"- **synthetic**: verdict={data.get('verdict', '?')}, "
                         f"{data.get('passed', '?')}/{data.get('effective_total', '?')} "
                         f"passing ({data.get('skipped', 0)} skipped)")
            failed = data.get("failed_tests") or []
            if failed:
                lines.append(f"  - FAILING: {', '.join(str(f) for f in failed)}")
        elif source == "github_ci":
            lines.append(f"- **github_ci**: green_rate={data.get('green_rate_24h', '?')}, "
                         f"runs={data.get('run_count', '?')}, "
                         f"last={data.get('last_run_status', '?')}")
            for wf in (data.get("failing_workflows") or [])[:3]:
                lines.append(f"  - failing: {str(wf)[:150]}")
        elif source == "cloudwatch":
            count = data.get("count", 0)
            lines.append(f"- **cloudwatch**: {count} log entries")
            for entry in (data.get("entries") or [])[:3]:
                if isinstance(entry, dict):
                    if entry.get("error"):
                        lines.append(f"  - {entry.get('source', '?')}: ERROR {str(entry['error'])[:120]}")
                    else:
                        lines.append(f"  - {entry.get('source', '?')}: "
                                     f"{str(entry.get('message', ''))[:150]}")
        elif source == "platform_events":
            events = data.get("recent_events") or []
            chains = data.get("active_heal_chains") or []
            lines.append(f"- **platform_events**: {len(events)} recent events, "
                         f"{len(chains)} active heal chains")
            for ev in events[:3]:
                if isinstance(ev, dict):
                    lines.append(f"  - {ev.get('event_type', '?')} "
                                 f"({ev.get('severity', '?')}) {ev.get('service', '')}")
            for ch in chains[:3]:
                if isinstance(ch, dict):
                    lines.append(f"  - heal {ch.get('chain', '?')} step={ch.get('step', '?')} "
                                 f"source={ch.get('source', '?')}")
        else:
            payload = {k: v for k, v in data.items() if k != "type"}
            lines.append(f"- **{source}**: {json.dumps(payload, default=str)[:200]}")

    return "\n".join(lines) if lines else "No evidence gathered"


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
        try:
            from nexus.sensors.tenant_health import check_tenant
            snap = await asyncio.to_thread(check_tenant, target_id)
            ctx = (snap.get("context") or {}) if isinstance(snap, dict) else {}
            stage = ctx.get("mission_stage", "unknown")
            status = snap.get("overall_status", "unknown") if isinstance(snap, dict) else "unknown"
            pr_count = ((snap.get("pipeline") or {}).get("pr_count", 0)
                        if isinstance(snap, dict) else 0)
            question = (
                f"Deep analysis of tenant {target_id} "
                f"(stage={stage}, status={status}, {pr_count} PRs). "
                f"Phase 1 found: {findings_txt}."
            )
        except Exception:
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
    evidence = result.get("evidence", {}) or {}
    evidence_summary = _format_evidence(evidence)

    return {
        "phase": "deep_analysis",
        "findings": diag.get("recommended_actions", []) or [],
        "confidence": diag.get("confidence", 60) or 60,
        "diagnosis": diag,
        "evidence": evidence,
        "evidence_summary": evidence_summary,
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
        if section.get("evidence_summary"):
            lines += ["", "### Evidence Gathered", section["evidence_summary"]]
        if section.get("execution_arn"):
            lines += ["", f"Step Function execution: `{section['execution_arn']}`"]
        if section.get("error"):
            lines += ["", f"_Phase error: {section['error']}_"]
        lines.append("")

    if level == "goal":
        try:
            from nexus.capabilities.sprint_context import format_for_report as _sp
            lines += ["", _sp()]
        except Exception:
            logger.debug("sprint_context injection failed", exc_info=True)
        try:
            from nexus.capabilities.cost_monitor import format_for_report as _cost
            lines += ["", _cost()]
        except Exception:
            logger.debug("cost_monitor injection failed", exc_info=True)
        try:
            from nexus.capabilities.bedrock_monitor import format_for_report as _br
            lines += ["", _br()]
        except Exception:
            logger.debug("bedrock_monitor injection failed", exc_info=True)
        try:
            from nexus.capabilities.onboarding_monitor import format_for_report as _ob
            lines += ["", _ob()]
        except Exception:
            logger.debug("onboarding_monitor injection failed", exc_info=True)
        try:
            from nexus.capabilities.predictions import (
                format_for_report as _pr, generate_predictions,
            )
            from nexus.capabilities.trend_analysis import compute_trend
            from nexus.sensors import ci_monitor
            ci = ci_monitor.check_ci() or {}
            trend = {}
            if ci.get("green_rate_24h") is not None:
                trend = compute_trend("ci_green_rate",
                                       float(ci["green_rate_24h"]))
            preds = generate_predictions(ci_data={"trend": trend})
            lines += ["", "## Predictions", _pr(preds)]
        except Exception:
            logger.debug("predictions injection failed", exc_info=True)

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
