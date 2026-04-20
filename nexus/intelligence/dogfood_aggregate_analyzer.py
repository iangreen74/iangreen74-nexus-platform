"""Cross-run clustering and aggregate analysis for dogfood diagnostics.

Pure Python analysis — no AWS calls. Operates on lists of analyzed runs
produced by dogfood_run_analyzer.analyze_run().
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def cluster_by_terminal_state(
    analyzed_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group runs by their SFN terminal state name.

    Returns a list of {state, count, run_ids} dicts sorted by count desc.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for run in analyzed_runs:
        terminal = run.get("terminal_state") or {}
        state = terminal.get("terminal_state") or "unknown"
        groups[state].append(run.get("run_id", "?"))
    return sorted(
        [{"state": s, "count": len(ids), "run_ids": ids} for s, ids in groups.items()],
        key=lambda x: x["count"],
        reverse=True,
    )


def cluster_by_outcome(
    analyzed_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group runs by their final status (success/failed/timeout/etc).

    Returns a list of {outcome, count, pct} dicts sorted by count desc.
    """
    counter = Counter(run.get("status", "unknown") for run in analyzed_runs)
    total = len(analyzed_runs) or 1
    return sorted(
        [
            {"outcome": outcome, "count": cnt, "pct": round(cnt / total * 100, 1)}
            for outcome, cnt in counter.items()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )


def top_error_messages(
    analyzed_runs: list[dict[str, Any]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Extract and rank the most frequent error messages across runs.

    Returns a list of {message, count} dicts, truncated to limit.
    """
    counter: Counter[str] = Counter()
    for run in analyzed_runs:
        # From SFN terminal state
        terminal = run.get("terminal_state") or {}
        err = terminal.get("error")
        if err:
            counter[err[:200]] += 1
        cause = terminal.get("cause")
        if cause:
            counter[cause[:200]] += 1
        # From CloudWatch error logs
        for line in run.get("error_logs", []):
            counter[line[:200]] += 1
    return [
        {"message": msg, "count": cnt}
        for msg, cnt in counter.most_common(limit)
    ]


def tenant_breakdown(
    analyzed_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Break down run outcomes by tenant_id.

    Returns a list of {tenant_id, total, success, failed, success_rate} dicts.
    """
    tenants: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "success": 0, "failed": 0}
    )
    for run in analyzed_runs:
        tid = run.get("tenant_id") or "unknown"
        tenants[tid]["total"] += 1
        if run.get("status") == "success":
            tenants[tid]["success"] += 1
        elif run.get("status") in ("failed", "timeout", "error"):
            tenants[tid]["failed"] += 1
    return sorted(
        [
            {
                "tenant_id": tid,
                **counts,
                "success_rate": round(
                    counts["success"] / counts["total"] * 100, 1
                ) if counts["total"] else 0.0,
            }
            for tid, counts in tenants.items()
        ],
        key=lambda x: x["total"],
        reverse=True,
    )


def stage_reachability(
    analyzed_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Count how many runs reached each pipeline stage.

    Infers stages from PipelineEvent event_types in each run.
    Returns a list of {stage, reached, pct} dicts.
    """
    stage_order = [
        "kickoff", "brief", "blueprint", "codegen",
        "pr", "ci", "deploy", "health", "pattern",
    ]
    stage_counts: Counter[str] = Counter()
    total = len(analyzed_runs) or 1

    for run in analyzed_runs:
        seen_stages: set[str] = set()
        for ev in run.get("pipeline_events", []):
            event_type = (ev.get("event_type") or "").lower()
            for stage in stage_order:
                if stage in event_type:
                    seen_stages.add(stage)
        for s in seen_stages:
            stage_counts[s] += 1

    return [
        {
            "stage": stage,
            "reached": stage_counts.get(stage, 0),
            "pct": round(stage_counts.get(stage, 0) / total * 100, 1),
        }
        for stage in stage_order
    ]
