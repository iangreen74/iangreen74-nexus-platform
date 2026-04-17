"""
Dogfood Sensor — polls pending DogfoodRun nodes and marks terminal state.

Runs every daemon cycle. For each pending DogfoodRun:
  - GET /deploy-progress/{tenant_id}?project_id={pid}
  - stage=live     → status=success
  - stage=failed   → status=failed
  - age > DOGFOOD_MAX_WAIT_MINUTES → status=timeout

Does not block. Returns a small report dict.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.capabilities import forgewing_api

logger = logging.getLogger("nexus.sensors.dogfood")

DEFAULT_MAX_WAIT_MINUTES = 90


def _decrement_if_batch(batch_id: str, success: bool) -> None:
    """Decrement the batch counter when a run reaches terminal state."""
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

        # Timeout check — don't even bother polling if already over cap.
        if age_seconds > max_wait_seconds:
            overwatch_graph.update_dogfood_run(
                run_id,
                status="timeout",
                completed_at=now.isoformat(),
            )
            _decrement_if_batch(batch_id, success=False)
            timed_out += 1
            logger.info("dogfood: run %s timed out after %.0fs", run_id, age_seconds)
            continue

        progress = forgewing_api.call_api(
            "GET", f"/deploy-progress/{tenant_id}?project_id={project_id}",
        )
        if not isinstance(progress, dict) or progress.get("error"):
            continue  # Transient — try again next cycle.

        stage = (progress.get("stage") or "").lower()
        if stage == "live":
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

    report = {
        "polled": len(pending),
        "completed": completed,
        "failed": failed,
        "timed_out": timed_out,
    }
    if completed or failed or timed_out:
        logger.info("dogfood sensor: %s", report)
    return report
