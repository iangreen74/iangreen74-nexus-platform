"""Daily snapshot capturer for Learning Intelligence trajectory.

Captures aggregate state as a LearningSnapshot node once per day.
Section 7 reads the last N snapshots to render real trend lines.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.intelligence import capability_matrix as cm
from nexus.intelligence import report_queries as q

logger = logging.getLogger(__name__)


def capture_snapshot() -> dict[str, Any]:
    """Capture aggregate state and persist as LearningSnapshot node."""
    runs = q.recent_dogfood_runs(hours=24)
    successes = [r for r in runs if r.get("status") == "success"]
    failures = [r for r in runs if r.get("status") in ("failed", "timeout")]
    attempts = q.deploy_attempts(hours=24)
    total_fp, unique_fp = q.pattern_fingerprint_counts()
    bedrock = q.bedrock_24h_cost()
    matrix = cm.status_counts()

    scores = [a["quality"] for a in attempts
              if isinstance(a.get("quality"), (int, float))]
    avg_q = (sum(scores) / len(scores)) if scores else -1.0

    snapshot = {
        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_24h": len(runs),
        "successes_24h": len(successes),
        "failures_24h": len(failures),
        "attempts_24h": len(attempts),
        "patterns_total": total_fp,
        "patterns_unique": unique_fp,
        "avg_quality": avg_q,
        "cost_24h": bedrock.get("cost_usd", 0.0),
        "proven": matrix.get("proven", 0),
        "architected": matrix.get("architected", 0),
    }

    try:
        overwatch_graph.query(
            "MERGE (s:LearningSnapshot {snapshot_date: $date}) "
            "SET s.created_at = $created_at, s.runs_24h = $runs_24h, "
            "s.successes_24h = $successes_24h, s.failures_24h = $failures_24h, "
            "s.attempts_24h = $attempts_24h, s.patterns_total = $patterns_total, "
            "s.patterns_unique = $patterns_unique, s.avg_quality = $avg_quality, "
            "s.cost_24h = $cost_24h, s.proven = $proven, "
            "s.architected = $architected",
            {"date": snapshot["snapshot_date"], **snapshot},
        )
        logger.info("Captured learning snapshot: %s", snapshot["snapshot_date"])
    except Exception:
        logger.exception("Failed to persist learning snapshot")

    return snapshot


def get_snapshots(days: int = 14) -> list[dict[str, Any]]:
    """Fetch last N daily snapshots for trend rendering."""
    try:
        rows = overwatch_graph.query(
            "MATCH (s:LearningSnapshot) "
            "RETURN s.snapshot_date AS date, s.runs_24h AS runs, "
            "s.successes_24h AS successes, s.attempts_24h AS attempts, "
            "s.patterns_unique AS patterns, s.avg_quality AS quality, "
            "s.cost_24h AS cost "
            "ORDER BY s.snapshot_date DESC LIMIT $days",
            {"days": days},
        ) or []
        return list(reversed(rows))
    except Exception:
        return []
