"""Scheduled autonomous diagnosis — runs Goal diagnosis every
DIAGNOSIS_INTERVAL_HOURS and records each result as an
OverwatchDiagnosisHistory node. The dashboard reads these for the
health timeline; pattern-learning tier will mine them later.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.config import MODE

logger = logging.getLogger(__name__)

DIAGNOSIS_INTERVAL_HOURS = 4
_POLL_EVERY_SEC = 3.0
_POLL_MAX_SEC = 420.0  # generous ceiling for slow Bedrock synthesis
_LABEL = "OverwatchDiagnosisHistory"

_scheduler_task: asyncio.Task | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_status(confidence: int, findings_count: int) -> str:
    if findings_count == 0 and confidence >= 85: return "healthy"
    if findings_count <= 3 and confidence >= 70: return "degraded"
    return "critical"


def _extract_key_findings(rec: dict[str, Any]) -> list[str]:
    return [f"{pc.get('phase', '?')}: {pc.get('findings', 0)} finding(s)"
            for pc in (rec.get("phases_completed") or []) if pc.get("findings")][:5]


async def run_scheduled_diagnosis() -> dict[str, Any]:
    """Start a Goal diagnosis, wait for it, record the result."""
    from nexus.capabilities.feature_diagnosis import (
        get_diagnosis, start_diagnosis,
    )

    started = await start_diagnosis("platform", level="goal")
    job_id = started.get("job_id")
    if not job_id:
        logger.warning("scheduled diagnosis: start returned no job_id (%s)", started)
        return {"error": started.get("error", "no job_id"), "recorded": False}

    elapsed = 0.0
    rec = started
    while elapsed < _POLL_MAX_SEC:
        await asyncio.sleep(_POLL_EVERY_SEC)
        elapsed += _POLL_EVERY_SEC
        rec = await get_diagnosis(job_id)
        if rec.get("status") in ("complete", "failed", "timeout"):
            break
    else:
        logger.warning("scheduled diagnosis %s did not complete within %ss",
                       job_id, _POLL_MAX_SEC)

    return _record_diagnosis(rec)


def _record_diagnosis(rec: dict[str, Any]) -> dict[str, Any]:
    from nexus import overwatch_graph

    confidence = int(rec.get("confidence", 0) or 0)
    findings_count = sum(
        int(pc.get("findings", 0) or 0)
        for pc in rec.get("phases_completed", []) or []
    )
    status = _classify_status(confidence, findings_count)

    report = rec.get("report") or ""
    summary = ""
    if isinstance(report, str) and "**Root cause:**" in report:
        summary = report.split("**Root cause:**", 1)[1].split("\n", 1)[0].strip()

    props = {
        "diagnosis_id": rec.get("job_id") or str(uuid.uuid4()),
        "level": rec.get("level", "goal"),
        "target_id": rec.get("target_id", "platform"),
        "confidence": confidence,
        "findings_count": findings_count,
        "phase_count": len(rec.get("phases_completed") or []),
        "status": status,
        "key_findings": json.dumps(_extract_key_findings(rec)),
        "report_summary": (summary or "All systems nominal")[:500],
        "job_status": rec.get("status", "unknown"),
        "duration_seconds": 0.0,
    }
    try:
        node_id = overwatch_graph._create_node(_LABEL, props)
        props["id"] = node_id
        props["recorded"] = True
        logger.info("scheduled diagnosis recorded: status=%s confidence=%d findings=%d",
                    status, confidence, findings_count)
    except Exception:
        logger.exception("failed to persist scheduled diagnosis")
        props["recorded"] = False
    return props


def _history_from_local(cutoff_iso: str) -> list[dict[str, Any]]:
    from nexus import overwatch_graph
    with overwatch_graph._lock:
        rows = [
            dict(n) for n in overwatch_graph._local_store.get(_LABEL, [])
            if n.get("created_at", "") >= cutoff_iso
        ]
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows


def get_diagnosis_history(hours: int = 72) -> list[dict[str, Any]]:
    """Return DiagnosisHistory nodes within the last `hours` hours, newest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    if MODE != "production":
        return _history_from_local(cutoff)
    from nexus import overwatch_graph
    rows = overwatch_graph.query(
        f"MATCH (n:{_LABEL}) WHERE n.created_at >= $cutoff "
        "RETURN n.id AS id, n.diagnosis_id AS diagnosis_id, n.level AS level, "
        "n.target_id AS target_id, n.confidence AS confidence, "
        "n.findings_count AS findings_count, n.phase_count AS phase_count, "
        "n.status AS status, n.key_findings AS key_findings, "
        "n.report_summary AS report_summary, n.job_status AS job_status, "
        "n.created_at AS created_at "
        "ORDER BY n.created_at DESC LIMIT 200",
        {"cutoff": cutoff},
    )
    for r in rows:
        kf = r.get("key_findings")
        if isinstance(kf, str):
            try:
                r["key_findings"] = json.loads(kf)
            except Exception:
                r["key_findings"] = []
    return rows


def get_health_trend(hours: int = 24) -> dict[str, Any]:
    """Compare the first and second half of `hours`. Returns trend direction
    plus counts so the UI can show a sparkline-style summary."""
    history = get_diagnosis_history(hours=hours)
    if len(history) < 2:
        return {"trend": "insufficient_data", "points": len(history)}
    midpoint = len(history) // 2
    older = history[midpoint:]  # earlier half (history is newest-first)
    newer = history[:midpoint]

    def _avg(rows: list[dict[str, Any]], key: str) -> float:
        vals = [float(r.get(key, 0) or 0) for r in rows]
        return sum(vals) / len(vals) if vals else 0.0

    conf_delta = _avg(newer, "confidence") - _avg(older, "confidence")
    find_delta = _avg(newer, "findings_count") - _avg(older, "findings_count")

    if conf_delta > 5 and find_delta <= 0:
        trend = "improving"
    elif conf_delta < -5 or find_delta > 1:
        trend = "degrading"
    else:
        trend = "stable"

    return {
        "trend": trend, "points": len(history),
        "confidence_delta": round(conf_delta, 1),
        "findings_delta": round(find_delta, 1),
        "latest_status": history[0].get("status", "unknown") if history else "unknown",
    }


async def _diagnosis_loop() -> None:
    # 30s initial delay so the first diagnosis sees warm caches.
    await asyncio.sleep(30)
    while True:
        try:
            await run_scheduled_diagnosis()
        except Exception:
            logger.exception("scheduled diagnosis cycle failed")
        await asyncio.sleep(DIAGNOSIS_INTERVAL_HOURS * 3600)


def start_scheduler() -> None:
    """Idempotent. No-op if no running event loop (e.g. unit test import)."""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    try:
        _scheduler_task = asyncio.create_task(_diagnosis_loop())
        logger.info("scheduled diagnosis loop started (every %dh)",
                    DIAGNOSIS_INTERVAL_HOURS)
    except RuntimeError:
        logger.debug("start_scheduler: no running event loop, skipping")
