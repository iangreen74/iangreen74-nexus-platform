"""
Learning Overview — aggregates Overwatch's training state for the
Learning tab. Pulls DeployAttempt + DogfoodRun records from Neptune
and shapes them into: training progress, dogfood runner stats,
pattern library, recent runs, and model fine-tuning status.

All queries go through `nexus.neptune_client`; in local mode they
return [] and the endpoint responds with zeroed counters + empty
lists (the tab renders cleanly with no data).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from nexus import neptune_client
from nexus.config import MODE

logger = logging.getLogger(__name__)

_CACHE_TTL = 30
_cache: tuple[dict[str, Any], float] = ({}, 0.0)

# Thresholds — these are the policy knobs for "ready to fine-tune" and
# "ready to bypass Sonnet". Kept here so tests can reach them.
FINETUNE_EXAMPLE_THRESHOLD = 1000
BYPASS_MIN_USES = 5
BYPASS_MIN_QUALITY = 0.9
DOGFOOD_COST_PER_RUN_USD = 0.15


def get_overview(force: bool = False) -> dict[str, Any]:
    """Main entry. 30-second cache guards Neptune from dashboard polling."""
    global _cache
    now = time.time()
    if not force and _cache[1] and (now - _cache[1]) < _CACHE_TTL:
        return _cache[0]

    training = _training()
    dogfood = _dogfood()
    patterns = _patterns()
    recent_runs = _recent_runs()
    model = _model_state(training)

    overview = {
        "training": training,
        "dogfood": dogfood,
        "patterns": patterns,
        "recent_runs": recent_runs,
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache = (overview, now)
    return overview


def _scalar(rows: list[dict[str, Any]], key: str, default: Any = 0) -> Any:
    if not rows or not isinstance(rows[0], dict):
        return default
    return rows[0].get(key, default)


def _training() -> dict[str, Any]:
    total_rows = neptune_client.query(
        "MATCH (d:DeployAttempt) WHERE d.deploy_success = true "
        "RETURN count(d) AS total_examples"
    ) or []
    total = int(_scalar(total_rows, "total_examples", 0) or 0)

    q_rows = neptune_client.query(
        "MATCH (d:DeployAttempt) WHERE d.deploy_success = true "
        "AND d.template_quality_score IS NOT NULL "
        "RETURN avg(toFloat(d.template_quality_score)) AS avg_quality"
    ) or []
    avg_raw = _scalar(q_rows, "avg_quality", None)
    avg_quality = None
    if avg_raw is not None:
        try:
            avg_quality = round(float(avg_raw), 3)
        except (TypeError, ValueError):
            avg_quality = None

    fp_rows = neptune_client.query(
        "MATCH (d:DeployAttempt) WHERE d.deploy_success = true "
        "RETURN DISTINCT d.fingerprint AS fp LIMIT 30"
    ) or []
    fingerprints = [r["fp"] for r in fp_rows
                    if isinstance(r, dict) and r.get("fp")]

    progress_pct = min(100, round(total / FINETUNE_EXAMPLE_THRESHOLD * 100)) \
        if FINETUNE_EXAMPLE_THRESHOLD else 0
    examples_needed = max(0, FINETUNE_EXAMPLE_THRESHOLD - total)

    runs_today = _runs_today()
    days_to_threshold: int | None = None
    if examples_needed > 0 and runs_today > 0:
        days_to_threshold = max(1, round(examples_needed / max(runs_today, 1)))

    return {
        "total_examples": total,
        "avg_quality": avg_quality,
        "coverage_count": len(fingerprints),
        "fingerprints": fingerprints,
        "progress_pct": progress_pct,
        "examples_needed": examples_needed,
        "days_to_threshold": days_to_threshold,
        "threshold": FINETUNE_EXAMPLE_THRESHOLD,
    }


def _runs_today() -> int:
    """DogfoodRuns started in the last 24h — used for pacing estimate."""
    rows = neptune_client.query(
        "MATCH (d:DogfoodRun) WHERE d.started_at >= $cutoff "
        "RETURN count(d) AS runs_today",
        {"cutoff": _cutoff_iso(24)},
    ) or []
    return int(_scalar(rows, "runs_today", 0) or 0)


def _cutoff_iso(hours: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc)
            - timedelta(hours=hours)).isoformat()


def _dogfood() -> dict[str, Any]:
    rows = neptune_client.query(
        "MATCH (d:DogfoodRun) "
        "RETURN count(d) AS total_runs, "
        "sum(CASE WHEN d.status = 'success' THEN 1 ELSE 0 END) AS successes, "
        "sum(CASE WHEN d.status = 'failed' THEN 1 ELSE 0 END) AS failures, "
        "sum(CASE WHEN d.status = 'timeout' THEN 1 ELSE 0 END) AS timeouts"
    ) or []
    row = rows[0] if rows and isinstance(rows[0], dict) else {}
    total = int(row.get("total_runs") or 0)
    successes = int(row.get("successes") or 0)
    failures = int(row.get("failures") or 0)
    timeouts = int(row.get("timeouts") or 0)
    success_rate = (successes / total) if total else 0.0

    runs_today = _runs_today()
    cost_today = round(runs_today * DOGFOOD_COST_PER_RUN_USD, 2)

    enabled, enabled_source = _resolve_enabled()

    return {
        "total_runs": total,
        "successes": successes,
        "failures": failures,
        "timeouts": timeouts,
        "success_rate": round(success_rate, 3),
        "runs_today": runs_today,
        "cost_today_usd": cost_today,
        "enabled": enabled,
        "enabled_source": enabled_source,
        "circuit_open": _dogfood_circuit_open(failures, total),
    }


def _dogfood_enabled() -> bool:
    return os.environ.get("DOGFOOD_ENABLED", "").lower() in ("1", "true", "yes")


def _resolve_enabled() -> tuple[bool, str]:
    """Return (enabled, source) reading Neptune config first, env fallback."""
    from nexus import overwatch_graph
    config = overwatch_graph.get_dogfood_config()
    neptune_flag = config.get("enabled")
    if neptune_flag is not None:
        return bool(neptune_flag), config.get("activated_by") or "neptune"
    env = _dogfood_enabled()
    return env, "env" if env else "off"


def _dogfood_circuit_open(failures: int, total: int) -> bool:
    """Circuit breaker: >50% failure rate with 10+ runs trips the breaker."""
    if total < 10:
        return False
    return failures / total > 0.5


def _recent_runs() -> list[dict[str, Any]]:
    rows = neptune_client.query(
        "MATCH (d:DogfoodRun) "
        "RETURN d.app_name AS app, d.fingerprint AS fp, "
        "d.status AS status, d.started_at AS started, "
        "d.completed_at AS completed "
        "ORDER BY d.started_at DESC LIMIT 20"
    ) or []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        started = _parse(r.get("started"))
        completed = _parse(r.get("completed"))
        elapsed = None
        if started and completed and completed >= started:
            elapsed = int((completed - started).total_seconds())
        out.append({
            "app": r.get("app") or "",
            "fingerprint": r.get("fp") or "",
            "status": r.get("status") or "",
            "started": started.isoformat() if started else None,
            "completed": completed.isoformat() if completed else None,
            "elapsed_seconds": elapsed,
        })
    return out


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _patterns() -> list[dict[str, Any]]:
    rows = neptune_client.query(
        "MATCH (d:DeployAttempt) WHERE d.deploy_success = true "
        "WITH d.fingerprint AS fp, count(d) AS uses, "
        "avg(toFloat(d.template_quality_score)) AS avg_q "
        f"WHERE uses >= {BYPASS_MIN_USES // 2 or 1} "
        "RETURN fp, uses, avg_q "
        "ORDER BY uses DESC LIMIT 20"
    ) or []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("fp"):
            continue
        uses = int(r.get("uses") or 0)
        avg_q_raw = r.get("avg_q")
        try:
            avg_q = round(float(avg_q_raw), 3) if avg_q_raw is not None else None
        except (TypeError, ValueError):
            avg_q = None
        ready = uses >= BYPASS_MIN_USES and (avg_q or 0) >= BYPASS_MIN_QUALITY
        out.append({
            "fingerprint": r["fp"],
            "uses": uses,
            "avg_quality": avg_q,
            "ready_to_bypass_sonnet": ready,
        })
    return out


def _model_state(training: dict[str, Any]) -> dict[str, Any]:
    total = training.get("total_examples", 0)
    return {
        "finetuning_runs": 0,
        "status": "ready" if total >= FINETUNE_EXAMPLE_THRESHOLD else "not_started",
    }


def trigger_finetuning() -> dict[str, Any]:
    """
    Stub for triggering the fine-tuning pipeline. Refuses to queue until
    the training threshold is met. When ready, logs the request and
    returns `{"status": "queued"}`. The actual SageMaker / Bedrock job
    wiring is future work — this is the single hook that will call it.
    """
    training = _training()
    if training.get("total_examples", 0) < FINETUNE_EXAMPLE_THRESHOLD:
        return {
            "error": (f"Not enough examples yet ("
                      f"{training['total_examples']}/"
                      f"{FINETUNE_EXAMPLE_THRESHOLD})."),
            "ready": False,
        }
    logger.warning(
        "[learning] trigger_finetuning requested with %d examples; "
        "stub queued (no actual job dispatched)",
        training.get("total_examples", 0),
    )
    return {
        "ok": True,
        "ready": True,
        "status": "queued",
        "examples": training.get("total_examples", 0),
        "note": "Stub — SageMaker/Bedrock job wiring pending",
    }


def clear_cache() -> None:
    global _cache
    _cache = ({}, 0.0)


# --- Batch runs -------------------------------------------------------------

VALID_BATCH_SIZES = (100, 200, 500, 1000)


def run_batch(count: int) -> dict[str, Any]:
    """
    Queue a batch of dogfood runs. Creates a DogfoodBatch node that the
    daemon's run_dogfood_cycle reads each cycle.
    """
    from nexus import overwatch_graph

    if count not in VALID_BATCH_SIZES:
        return {"error": f"count must be one of {VALID_BATCH_SIZES}"}

    existing = overwatch_graph.get_active_batch()
    if existing:
        return {
            "error": "A batch is already running",
            "active_batch": existing.get("batch_id"),
            "remaining": existing.get("remaining"),
        }

    import uuid
    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    overwatch_graph.create_dogfood_batch(batch_id, count)
    overwatch_graph.set_dogfood_config(enabled=True, activated_by="batch")

    cost = round(count * DOGFOOD_COST_PER_RUN_USD, 2)
    runs_today = _runs_today()
    rate = max(runs_today, 1)
    est_days = max(1, round(count / rate))

    logger.info("[learning] batch queued: %s (%d runs, ~$%.2f)", batch_id, count, cost)
    return {
        "ok": True,
        "batch_id": batch_id,
        "count": count,
        "estimated_cost_usd": cost,
        "estimated_days": est_days,
    }


def batch_status() -> dict[str, Any]:
    """Return the active batch if one is running."""
    from nexus import overwatch_graph
    batch = overwatch_graph.get_active_batch()
    if not batch:
        return {"active": False}
    requested = int(batch.get("requested") or 0)
    completed = int(batch.get("completed") or 0)
    successes = int(batch.get("successes") or 0)
    return {
        "active": True,
        "batch_id": batch.get("batch_id"),
        "requested": requested,
        "remaining": int(batch.get("remaining") or 0),
        "completed": completed,
        "successes": successes,
        "failures": int(batch.get("failures") or 0),
        "success_rate": round(successes / completed, 3) if completed else 0.0,
        "started_at": batch.get("started_at"),
    }


# --- CI/CD metrics ----------------------------------------------------------


def cicd_metrics() -> dict[str, Any]:
    """Aggregate CI/CD performance data for the Learning tab."""
    cutoff_7d = _cutoff_iso(7 * 24)
    cutoff_48h = _cutoff_iso(48)

    total_rows = neptune_client.query(
        "MATCH (d:DeployAttempt) RETURN count(d) AS total"
    ) or []
    total = int(_scalar(total_rows, "total", 0) or 0)

    rate_rows = neptune_client.query(
        "MATCH (d:DeployAttempt) WHERE d.started_at > $cutoff "
        "RETURN count(d) AS total, "
        "sum(CASE WHEN d.deploy_success = true THEN 1 ELSE 0 END) AS successes",
        {"cutoff": cutoff_7d},
    ) or []
    recent_total = int(_scalar(rate_rows, "total", 0) or 0)
    recent_successes = int(_scalar(rate_rows, "successes", 0) or 0)
    success_rate = round(recent_successes / recent_total, 3) if recent_total else 0.0

    time_rows = neptune_client.query(
        "MATCH (d:DeployAttempt) WHERE d.deploy_success = true "
        "AND d.time_to_healthy IS NOT NULL AND d.started_at > $cutoff "
        "RETURN avg(toFloat(d.time_to_healthy)) AS avg_seconds",
        {"cutoff": cutoff_7d},
    ) or []
    avg_time_raw = _scalar(time_rows, "avg_seconds", None)
    avg_time: float | None = None
    if avg_time_raw is not None:
        try:
            avg_time = round(float(avg_time_raw), 1)
        except (TypeError, ValueError):
            pass

    bypass_rows = neptune_client.query(
        "MATCH (d:DeployAttempt) WHERE d.started_at > $cutoff "
        "AND d.bypass = true RETURN count(d) AS bypasses",
        {"cutoff": cutoff_7d},
    ) or []
    bypasses = int(_scalar(bypass_rows, "bypasses", 0) or 0)
    bypass_rate = round(bypasses / recent_total, 3) if recent_total else 0.0

    fail_rows = neptune_client.query(
        "MATCH (d:DeploymentProgress) WHERE d.stage = 'failed' "
        "AND d.updated_at > $cutoff "
        "RETURN d.tenant_id AS tid, "
        "substring(coalesce(d.message,''),0,120) AS msg, "
        "d.updated_at AS failed_at, "
        "d.diagnosis_json AS diag "
        "ORDER BY d.updated_at DESC LIMIT 20",
        {"cutoff": cutoff_48h},
    ) or []
    failures: list[dict[str, Any]] = []
    for r in fail_rows:
        if not isinstance(r, dict):
            continue
        failures.append({
            "tenant_id": r.get("tid") or "",
            "message": (r.get("msg") or "")[:120],
            "failed_at": r.get("failed_at"),
            "has_diagnosis": bool(r.get("diag")),
        })

    return {
        "total_deploys": total,
        "success_rate_7d": success_rate,
        "avg_time_to_healthy_seconds": avg_time,
        "bypass_rate_7d": bypass_rate,
        "bypass_count_7d": bypasses,
        "recent_total_7d": recent_total,
        "active_failures": len(failures),
        "recent_failures": failures,
    }


# --- Intelligence score -----------------------------------------------------

INTELLIGENCE_BASE = 60


def intelligence_score() -> dict[str, Any]:
    """
    Composite score tracking how well the deploy system performs as data
    accumulates. Formula:
      60 base + bypass_count*4 + examples/50 + quality*10, capped at 100.
    """
    training = _training()
    patterns = _patterns()
    bypass_ready = sum(1 for p in patterns if p.get("ready_to_bypass_sonnet"))
    total_examples = training.get("total_examples", 0)
    avg_quality = training.get("avg_quality") or 0.0

    from_bypasses = min(bypass_ready * 4, 20)
    from_examples = min(int(total_examples / 50), 10)
    from_quality = min(round(avg_quality * 10, 1), 10)
    score = min(100, INTELLIGENCE_BASE + from_bypasses + from_examples + from_quality)

    history_rows = neptune_client.query(
        "MATCH (d:DogfoodRun) WHERE d.status = 'success' "
        "RETURN toString(date(d.completed_at)) AS day, count(d) AS runs "
        "ORDER BY day ASC"
    ) or []
    history: list[dict[str, Any]] = []
    cumulative = 0
    for r in history_rows:
        if not isinstance(r, dict) or not r.get("day"):
            continue
        cumulative += int(r.get("runs") or 0)
        day_score = min(100, INTELLIGENCE_BASE + min(int(cumulative / 50), 10))
        history.append({
            "date": r["day"],
            "score": day_score,
            "examples": cumulative,
        })

    next_milestone: dict[str, Any] | None = None
    if score < 70:
        next_milestone = {"score": 70, "description": "Reach first bypass-ready pattern"}
    elif score < 80:
        next_milestone = {"score": 80, "description": "3+ bypass-ready patterns"}
    elif score < 90:
        next_milestone = {"score": 90, "description": "500+ examples with >90% quality"}
    elif score < 100:
        next_milestone = {"score": 100, "description": "All patterns bypass-ready + fine-tuned model"}

    return {
        "current_score": round(score),
        "score_history": history,
        "score_breakdown": {
            "base": INTELLIGENCE_BASE,
            "from_bypasses": from_bypasses,
            "from_examples": from_examples,
            "from_quality": round(from_quality, 1),
        },
        "next_milestone": next_milestone,
    }


# --- Auto-schedule ----------------------------------------------------------

VALID_DAILY_RUNS = (0, 10, 50, 100)


def get_schedule() -> dict[str, Any]:
    from nexus import overwatch_graph
    sched = overwatch_graph.get_dogfood_schedule()
    return {
        "runs_per_day": int(sched.get("runs_per_day") or 0),
        "enabled": bool(sched.get("enabled")),
        "next_run": sched.get("next_run") or None,
    }


def set_schedule(runs_per_day: int) -> dict[str, Any]:
    from nexus import overwatch_graph
    if runs_per_day not in VALID_DAILY_RUNS:
        return {"error": f"runs_per_day must be one of {VALID_DAILY_RUNS}"}
    overwatch_graph.set_dogfood_schedule(runs_per_day, enabled=runs_per_day > 0)
    cost = round(runs_per_day * DOGFOOD_COST_PER_RUN_USD, 2)
    logger.info("[learning] schedule updated: %d runs/day (~$%.2f/day)",
                runs_per_day, cost)
    return {
        "ok": True,
        "runs_per_day": runs_per_day,
        "enabled": runs_per_day > 0,
        "cost_per_day_usd": cost,
    }
