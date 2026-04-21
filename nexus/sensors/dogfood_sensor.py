"""Dogfood Sensor — polls pending DogfoodRun nodes for terminal state."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from nexus import neptune_client, overwatch_graph
from nexus.capabilities import forgewing_api

logger = logging.getLogger("nexus.sensors.dogfood")

DEFAULT_MAX_WAIT_MINUTES = 90
INACTIVITY_THRESHOLD_MINUTES = int(os.environ.get("DOGFOOD_INACTIVITY_MIN", "20"))

# v1 writes 'live'; v2 writes 'deploy_complete' (terminal) or 'healthy'.
V2_SUCCESS_STAGES = ("live", "deploy_complete", "healthy")


def _decrement_if_batch(batch_id: str, success: bool) -> None:
    if not batch_id:
        return
    overwatch_graph.decrement_batch(batch_id, success)


def _max_wait_minutes() -> int:
    try:
        return int(os.environ.get("DOGFOOD_MAX_WAIT_MINUTES", DEFAULT_MAX_WAIT_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_MAX_WAIT_MINUTES


def _parse_iso(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _has_blueprint(project_id: str) -> bool:
    rows = neptune_client.query(
        "MATCH (bp:ProductBlueprint {project_id: $pid}) RETURN count(bp) AS c",
        {"pid": project_id},
    )
    return bool(rows and isinstance(rows[0], dict) and rows[0].get("c", 0) > 0)


def _has_tasks(project_id: str) -> bool:
    rows = neptune_client.query(
        "MATCH (t:MissionTask {project_id: $pid}) RETURN count(t) AS c",
        {"pid": project_id},
    )
    return bool(rows and isinstance(rows[0], dict) and rows[0].get("c", 0) > 0)


def _maybe_auto_approve(tenant_id: str, project_id: str, run: dict) -> bool:
    """Drive Plan-screen flow for batch runs. Returns True if action taken."""
    batch_id = run.get("batch_id") or ""
    if not batch_id:
        return False

    try:
        status = forgewing_api.call_api(
            "GET", f"/status/{tenant_id}?project_id={project_id}")
        if not isinstance(status, dict):
            return False
        mission_stage = (status.get("mission_stage") or "").lower()
        if mission_stage != "brief_pending_approval":
            return False

        if _has_tasks(project_id):
            return False

        if _has_blueprint(project_id):
            result = forgewing_api.call_api(
                "POST", f"/projects/{tenant_id}/{project_id}/approve-blueprint")
            if isinstance(result, dict) and result.get("status") == "approved":
                logger.info("dogfood: auto-approved blueprint for %s/%s (%s tasks)",
                            tenant_id[:12], project_id[:7],
                            result.get("tasks_created", "?"))
                return True
            logger.debug("dogfood: approve-blueprint returned %s", result)
            return False

        vision = _get_product_vision(tenant_id) or "A simple web application"
        result = forgewing_api.call_api(
            "POST", f"/projects/{tenant_id}/{project_id}/synthesize",
            data={"product_vision": vision},
        )
        if isinstance(result, dict) and not result.get("error"):
            logger.info("dogfood: synthesis triggered for %s/%s",
                        tenant_id[:12], project_id[:7])
            overwatch_graph.update_dogfood_run(
                run.get("id", ""),
                synthesis_triggered_at=datetime.now(timezone.utc).isoformat(),
            )
            return True
        logger.warning("dogfood: synthesis failed for %s/%s: %s",
                       tenant_id[:12], project_id[:7], result)
        return False
    except Exception:
        logger.debug("dogfood: auto-approve/synthesize failed for %s",
                     tenant_id[:12], exc_info=True)
        return False


def _get_product_vision(tenant_id: str) -> str:
    rows = neptune_client.query(
        "MATCH (u:UserContext {tenant_id: $tid}) RETURN u.product_vision AS v LIMIT 1",
        {"tid": tenant_id})
    return (rows[0].get("v") or "").strip() if rows and isinstance(rows[0], dict) else ""


def check_dogfood_runs() -> dict[str, Any]:
    """Single pass: poll deploy-progress for every pending DogfoodRun."""
    pending = overwatch_graph.list_dogfood_runs(status="pending", limit=50)
    if not pending:
        return {"polled": 0, "completed": 0, "failed": 0, "timed_out": 0}

    now = datetime.now(timezone.utc)
    max_wait_seconds = _max_wait_minutes() * 60
    completed = failed = timed_out = 0

    for run in pending:
        tenant_id = run.get("tenant_id", "")
        project_id = run.get("project_id", "")
        run_id = run.get("id", "")
        if not tenant_id or not project_id or not run_id:
            continue

        started = _parse_iso(run.get("started_at"))
        age_seconds = (now - started).total_seconds() if started else 0
        batch_id = run.get("batch_id") or ""

        if age_seconds > max_wait_seconds:
            overwatch_graph.update_dogfood_run(
                run_id, status="timeout", completed_at=now.isoformat(),
            )
            _decrement_if_batch(batch_id, success=False)
            timed_out += 1
            logger.info("dogfood: run %s timed out after %.0fs", run_id, age_seconds)
            continue

        progress = forgewing_api.call_api(
            "GET", f"/deploy-progress/{tenant_id}?project_id={project_id}",
        )
        if not isinstance(progress, dict) or progress.get("error"):
            continue

        stage = (progress.get("stage") or "").lower()
        prev = run.get("last_observed_stage", "")
        if stage and stage != prev:
            updates = {"last_observed_stage": stage}
            if prev:
                updates["last_progress_at"] = now.isoformat()
            overwatch_graph.update_dogfood_run(run_id, **updates)

        if stage in V2_SUCCESS_STAGES:
            overwatch_graph.update_dogfood_run(
                run_id, status="success", completed_at=now.isoformat(),
            )
            _decrement_if_batch(batch_id, success=True)
            completed += 1
        elif stage in ("failed", "error"):
            overwatch_graph.update_dogfood_run(
                run_id, status="failed", completed_at=now.isoformat(),
                failure_message=(progress.get("message") or "")[:500],
            )
            _decrement_if_batch(batch_id, success=False)
            failed += 1
        elif stage == "not_started":
            acted = _maybe_auto_approve(tenant_id, project_id, run)
            if acted:
                overwatch_graph.update_dogfood_run(
                    run_id, last_progress_at=now.isoformat())
            elif batch_id:
                last = _parse_iso(run.get("last_progress_at")) or started
                inactive = (now - last).total_seconds()
                if inactive > INACTIVITY_THRESHOLD_MINUTES * 60:
                    msg = f"No progress for {inactive / 60:.0f}m."
                    overwatch_graph.update_dogfood_run(
                        run_id, status="failed", completed_at=now.isoformat(),
                        outcome="stalled", failure_message=msg)
                    _decrement_if_batch(batch_id, success=False)
                    failed += 1
                    logger.info("dogfood: run %s stalled (%.0fm inactive)",
                                run_id, inactive / 60)

    report = {
        "polled": len(pending),
        "completed": completed,
        "failed": failed,
        "timed_out": timed_out,
    }
    if completed or failed or timed_out:
        logger.info("dogfood sensor: %s", report)
    return report
