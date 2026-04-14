"""
CI Cycle Loop — drives ci_heartbeat → ci_healer → ci_patterns.

CI hangs are time-critical (minutes, not hours), so this runs on its own
asyncio loop independent of the main diagnosis scheduler. Every
CI_CYCLE_INTERVAL_SEC it:

  1. Calls ci_heartbeat.check_ci_heartbeat() for hung jobs.
  2. For each hung job whose (job, runner) pair already hit in the
     previous cycle, escalates to ci_healer.heal_hung_ci() — one spurious
     reading doesn't warrant a kill, two consecutive readings do.
  3. Calls ci_patterns.learn_ci_patterns() to refresh the anti-pattern
     library and dashboard banners.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("nexus.capabilities.ci_cycle")

CI_CYCLE_INTERVAL_SEC = 120

_scheduler_task: asyncio.Task[None] | None = None
# Set of (job_id, runner_name) pairs observed hung on the previous cycle;
# only escalate to ci_healer on the second consecutive reading.
_prev_hung: set[tuple[Any, str]] = set()


async def run_ci_cycle() -> dict[str, Any]:
    """One iteration of the heartbeat → healer → patterns chain."""
    global _prev_hung
    from nexus.capabilities import ci_heartbeat, ci_healer, ci_patterns

    try:
        hb = await asyncio.to_thread(ci_heartbeat.check_ci_heartbeat)
    except Exception:
        logger.exception("ci_heartbeat failed")
        hb = {"hung": []}

    hung = hb.get("hung") or []
    now_hung = {(h.get("job_id"), h.get("runner_name") or "") for h in hung}
    heal_results: list[dict[str, Any]] = []
    for h in hung:
        key = (h.get("job_id"), h.get("runner_name") or "")
        if key not in _prev_hung:
            continue  # first sighting — wait for confirmation
        try:
            heal_results.append(
                await asyncio.to_thread(ci_healer.heal_hung_ci, h))
        except Exception:
            logger.exception("ci_healer failed for %s", key)
    _prev_hung = now_hung

    try:
        patterns = await asyncio.to_thread(ci_patterns.learn_ci_patterns)
    except Exception:
        logger.exception("ci_patterns failed")
        patterns = {}

    return {"heartbeat": hb, "heals": heal_results, "patterns": patterns}


async def _ci_loop() -> None:
    await asyncio.sleep(15)  # small warmup to let the server come up
    while True:
        try:
            await run_ci_cycle()
        except Exception:
            logger.exception("ci_cycle iteration failed")
        await asyncio.sleep(CI_CYCLE_INTERVAL_SEC)


def start_ci_cycle() -> None:
    """Idempotent. No-op if no running event loop (e.g. unit-test import)."""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    try:
        _scheduler_task = asyncio.create_task(_ci_loop())
        logger.info("ci_cycle started (every %ds)", CI_CYCLE_INTERVAL_SEC)
    except RuntimeError:
        logger.debug("start_ci_cycle: no running event loop, skipping")
