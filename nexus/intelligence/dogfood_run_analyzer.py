"""Per-run deep analyzer for dogfood diagnostic reports.

Takes an OverwatchDogfoodRun dict and enriches it with pipeline events,
SFN execution details, terminal state, and error logs.
"""
from __future__ import annotations

import logging
from typing import Any

from nexus import overwatch_graph
from nexus.intelligence import dogfood_logs_probe, dogfood_sfn_probe

logger = logging.getLogger("nexus.intelligence.dogfood_run_analyzer")


def _get_pipeline_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch PipelineEvent nodes correlated with this run's project."""
    project_id = run.get("project_id")
    if not project_id:
        return []
    try:
        return overwatch_graph.query(
            "MATCH (e:OverwatchPipelineEvent {project_id: $pid}) "
            "RETURN e.event_id AS event_id, e.event_type AS event_type, "
            "e.emitted_at AS emitted_at, e.correlation_id AS correlation_id, "
            "e.payload AS payload "
            "ORDER BY e.emitted_at",
            {"pid": project_id},
        )
    except Exception:
        logger.exception("Pipeline event query failed for project %s", project_id)
        return []


def _find_sfn_execution(run: dict[str, Any]) -> dict[str, Any] | None:
    """Find the SFN execution matching this run by name or time correlation."""
    run_id = run.get("id", "")
    app_name = run.get("app_name", "")
    started_at = run.get("started_at", "")

    # Try to find by listing recent executions and matching by name/time
    try:
        executions = dogfood_sfn_probe.list_recent_executions(hours=12, limit=100)
    except Exception:
        logger.exception("SFN execution lookup failed for run %s", run_id)
        return None

    # Match by execution name containing run_id or app_name
    for ex in executions:
        name = ex.get("name", "")
        if run_id and run_id in name:
            return ex
        if app_name and app_name in name:
            return ex

    # Fallback: match by closest start time
    if started_at and executions:
        return executions[0]  # already sorted newest-first

    return None


def _get_error_logs(run: dict[str, Any]) -> list[str]:
    """Fetch error logs around the run's completion time."""
    timestamp = run.get("completed_at") or run.get("started_at")
    if not timestamp:
        return []
    try:
        events = dogfood_logs_probe.fetch_logs_around_time(
            iso_timestamp=timestamp,
            window_minutes=5,
            limit=200,
        )
        return dogfood_logs_probe.find_error_lines(events)
    except Exception:
        logger.exception("Error log fetch failed for run %s", run.get("id"))
        return []


def analyze_run(run: dict[str, Any]) -> dict[str, Any]:
    """Deep-analyze a single OverwatchDogfoodRun.

    Enriches the run with pipeline events, SFN execution state,
    terminal state details, and error logs (if failed).

    Args:
        run: An OverwatchDogfoodRun dict from overwatch_graph.

    Returns:
        Enriched dict with original run fields plus analysis data.
    """
    result: dict[str, Any] = {
        "run_id": run.get("id"),
        "app_name": run.get("app_name"),
        "tenant_id": run.get("tenant_id"),
        "project_id": run.get("project_id"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "batch_id": run.get("batch_id"),
    }

    # Pipeline events
    result["pipeline_events"] = _get_pipeline_events(run)

    # SFN execution
    sfn_exec = _find_sfn_execution(run)
    result["sfn_execution"] = sfn_exec

    # Terminal state (only if we found an SFN execution)
    if sfn_exec and sfn_exec.get("execution_arn"):
        terminal = dogfood_sfn_probe.get_execution_terminal_state(
            sfn_exec["execution_arn"]
        )
        result["terminal_state"] = terminal
    else:
        result["terminal_state"] = None

    # Error logs (only for non-success runs)
    if run.get("status") in ("failed", "timeout", "error"):
        result["error_logs"] = _get_error_logs(run)
    else:
        result["error_logs"] = []

    return result
