"""
Deploy Cycle Loop — deploy-critical polling that must run every cycle.

Runs on its own asyncio loop independent of the diagnosis scheduler and
CI cycle. Every DEPLOY_CYCLE_INTERVAL_SEC (60s) it:

  1. dogfood_sensor: polls pending DogfoodRun nodes for outcome
  2. dogfood_reconciler: cleans up completed/timed-out runs
  3. dogfood schedule: checks DogfoodSchedule for auto-batch queuing
  4. batch completion: detects when a batch finishes and auto-pauses

These are all cheap Neptune reads + Forgewing API calls — well within
a 30s budget. The expensive part (creating repos, triggering deploys)
stays in run_dogfood_cycle, which only fires via triage or batch.

Tier 1 (this loop): always runs, 30s timeout, deploy-critical.
Tier 2 (ci_cycle): always runs, 120s interval, CI-critical.
Tier 3 (scheduled_diagnosis): runs every 4h, heavy Bedrock synthesis.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("nexus.capabilities.deploy_cycle")

DEPLOY_CYCLE_INTERVAL_SEC = 60
WATCHDOG_INTERVAL_SEC = 60
_scheduler_task: asyncio.Task[None] | None = None
_watchdog_task: asyncio.Task[None] | None = None
_respawn_count = 0


async def run_deploy_cycle() -> dict[str, Any]:
    """
    One iteration of deploy-critical polling. Each step has its own
    try/except so a failure in one never prevents others from running.
    The outer loop also wraps this call — belt AND suspenders.
    """
    results: dict[str, Any] = {}

    # 1. Poll pending dogfood runs
    try:
        from nexus.sensors.dogfood_sensor import check_dogfood_runs
        sensor = await asyncio.wait_for(
            asyncio.to_thread(check_dogfood_runs), timeout=30)
        results["dogfood_sensor"] = sensor
    except asyncio.TimeoutError:
        logger.warning("deploy_cycle: dogfood_sensor timed out (30s)")
        results["dogfood_sensor"] = {"error": "timeout"}
    except Exception:
        logger.exception("deploy_cycle: dogfood_sensor failed")
        results["dogfood_sensor"] = {"error": "exception"}

    # 2. Reconcile completed runs
    try:
        from nexus.sensors.dogfood_reconciler import reconcile_dogfood
        recon = await asyncio.wait_for(
            asyncio.to_thread(reconcile_dogfood), timeout=30)
        results["dogfood_reconciler"] = recon
    except asyncio.TimeoutError:
        logger.warning("deploy_cycle: dogfood_reconciler timed out (30s)")
        results["dogfood_reconciler"] = {"error": "timeout"}
    except Exception:
        logger.exception("deploy_cycle: dogfood_reconciler failed")
        results["dogfood_reconciler"] = {"error": "exception"}

    # 3. Kick off dogfood run if enabled/batch active
    try:
        results["dogfood_kick"] = await asyncio.wait_for(
            asyncio.to_thread(_kick_dogfood_if_needed), timeout=60)
    except asyncio.TimeoutError:
        logger.warning("deploy_cycle: dogfood kick timed out (60s)")
        results["dogfood_kick"] = {"error": "timeout"}
    except Exception:
        logger.exception("deploy_cycle: dogfood kick failed")
        results["dogfood_kick"] = {"error": "exception"}

    # 4. Check auto-schedule
    try:
        results["schedule"] = await asyncio.to_thread(_check_schedule)
    except Exception:
        logger.exception("deploy_cycle: schedule check failed")

    # 5. Check batch completion → auto-pause
    try:
        results["batch"] = await asyncio.to_thread(_check_batch_completion)
    except Exception:
        logger.exception("deploy_cycle: batch completion check failed")

    # 6. Daily learning snapshot (idempotent by date)
    try:
        await asyncio.to_thread(_maybe_capture_snapshot)
    except Exception:
        logger.exception("deploy_cycle: snapshot capture failed")

    kicked = results.get("dogfood_kick", {})
    if not kicked.get("skipped"):
        logger.info("deploy_cycle: tick done (kick=%s, sensor=%s, recon=%s)",
                     kicked.get("status") or kicked.get("error", "—"),
                     "ok" if "error" not in (results.get("dogfood_sensor") or {}) else "err",
                     "ok" if "error" not in (results.get("dogfood_reconciler") or {}) else "err")
    return results


def _kick_dogfood_if_needed() -> dict[str, Any]:
    """
    If the runner is enabled or a batch is active, kick off one dogfood
    deploy per cycle. This is the bridge between "batch queued in Neptune"
    and "repos actually being created + deployed."
    """
    from nexus import overwatch_graph
    from nexus.capabilities.dogfood_capability import _is_enabled, run_dogfood_cycle

    batch = overwatch_graph.get_active_batch()
    if not _is_enabled() and not batch:
        return {"skipped": True, "reason": "not enabled, no batch"}

    result = run_dogfood_cycle()
    status = result.get("status") or result.get("reason") or "unknown"
    logger.info("deploy_cycle: dogfood kick → %s (app=%s)",
                status, result.get("app", "—"))

    # Stamp batch_id on the DogfoodRun so the sensor can decrement
    # the correct batch when the run reaches terminal state.
    if batch and not result.get("skipped") and result.get("run_id"):
        batch_id = batch.get("batch_id") or ""
        overwatch_graph.update_dogfood_run(result["run_id"], batch_id=batch_id)
        logger.info("deploy_cycle: stamped batch_id=%s on run %s",
                     batch_id, result["run_id"])

    return result


_last_snapshot_date: str = ""


def _maybe_capture_snapshot() -> None:
    """Capture one learning snapshot per day."""
    global _last_snapshot_date
    today = datetime.now(timezone.utc).date().isoformat()
    if _last_snapshot_date == today:
        return
    try:
        from nexus.intelligence.learning_snapshot import capture_snapshot
        capture_snapshot()
        _last_snapshot_date = today
    except Exception:
        logger.exception("deploy_cycle: snapshot capture failed")


def _check_schedule() -> dict[str, Any]:
    """If a DogfoodSchedule exists and next_run is past, queue a batch."""
    from nexus import overwatch_graph
    from nexus.config import MODE

    sched = overwatch_graph.get_dogfood_schedule()
    if not sched.get("enabled") or not sched.get("runs_per_day"):
        return {"skipped": True, "reason": "schedule disabled"}

    next_run = sched.get("next_run") or ""
    now = datetime.now(timezone.utc)

    if next_run:
        try:
            next_dt = datetime.fromisoformat(str(next_run).replace("Z", "+00:00"))
            if next_dt > now:
                return {"skipped": True, "reason": "not yet",
                        "next_run": next_run}
        except Exception:
            pass

    # Time to queue a batch
    batch = overwatch_graph.get_active_batch()
    if batch:
        return {"skipped": True, "reason": "batch already active"}

    from nexus import learning_overview as lo
    rpd = int(sched.get("runs_per_day") or 0)
    result = lo.run_batch(rpd)

    # Set next_run to tomorrow midnight UTC
    tomorrow = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1))
    if MODE != "production":
        with overwatch_graph._lock:
            rows = overwatch_graph._local_store.get("OverwatchDogfoodSchedule", [])
            if rows:
                rows[0]["next_run"] = tomorrow.isoformat()
    else:
        overwatch_graph.query(
            "MATCH (s:OverwatchDogfoodSchedule {schedule_id: 'main'}) "
            "SET s.next_run = $nr",
            {"nr": tomorrow.isoformat()},
        )

    logger.info("deploy_cycle: auto-scheduled batch of %d runs", rpd)
    return {"queued": True, "runs": rpd, "batch": result.get("batch_id")}


def _check_batch_completion() -> dict[str, Any]:
    """When an active batch hits remaining=0, auto-pause DogfoodConfig."""
    import os
    from nexus import overwatch_graph

    batch = overwatch_graph.get_active_batch()
    if batch:
        return {"active": True, "remaining": batch.get("remaining")}

    config = overwatch_graph.get_dogfood_config()
    if config.get("enabled") and config.get("activated_by") == "batch":
        if not os.environ.get("DOGFOOD_ENABLED", "").lower() in ("true", "1", "yes"):
            overwatch_graph.set_dogfood_config(enabled=False, activated_by="auto-pause")
            logger.info("deploy_cycle: batch complete, auto-paused DogfoodConfig")
            return {"auto_paused": True}
    return {"active": False}


async def _deploy_loop() -> None:
    await asyncio.sleep(10)
    while True:
        try:
            await run_deploy_cycle()
        except asyncio.CancelledError:
            logger.info("_deploy_loop cancelled, exiting")
            raise
        except Exception:
            logger.exception("deploy_cycle tick failed — loop continues")
        await asyncio.sleep(DEPLOY_CYCLE_INTERVAL_SEC)


async def _deploy_loop_watchdog() -> None:
    """Respawn _deploy_loop if it dies. Checks every WATCHDOG_INTERVAL_SEC."""
    global _scheduler_task, _respawn_count
    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
            if _scheduler_task is None or _scheduler_task.done():
                exc = None
                if _scheduler_task is not None and not _scheduler_task.cancelled():
                    try:
                        exc = _scheduler_task.exception()
                    except Exception:
                        pass
                _respawn_count += 1
                logger.error(
                    "watchdog: _deploy_loop died (respawn #%d, reason=%s) — restarting",
                    _respawn_count, exc)
                _scheduler_task = asyncio.create_task(_deploy_loop())
        except asyncio.CancelledError:
            logger.info("watchdog cancelled, exiting")
            raise
        except Exception:
            logger.exception("watchdog tick failed — watchdog continues")


def start_deploy_cycle() -> None:
    """Start both the deploy loop and its watchdog. Idempotent."""
    global _scheduler_task, _watchdog_task
    try:
        if _scheduler_task is None or _scheduler_task.done():
            _scheduler_task = asyncio.create_task(_deploy_loop())
            logger.info("deploy_cycle started (every %ds)", DEPLOY_CYCLE_INTERVAL_SEC)
        if _watchdog_task is None or _watchdog_task.done():
            _watchdog_task = asyncio.create_task(_deploy_loop_watchdog())
            logger.info("deploy_cycle watchdog started (every %ds)", WATCHDOG_INTERVAL_SEC)
    except RuntimeError:
        logger.debug("start_deploy_cycle: no running event loop, skipping")
