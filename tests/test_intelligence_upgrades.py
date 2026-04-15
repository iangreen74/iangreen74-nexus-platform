"""Tests for the 5 Overwatch intelligence improvements."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import (  # noqa: E402
    predictions as preds_mod,
    session_context as sc_mod,
    sprint_context as sprint_mod,
    trend_analysis as trend_mod,
    timeline_resolution as tr_mod,
)


def _reset():
    overwatch_graph.reset_local_store()
    sprint_mod.reset()


# --- 1. Trends --------------------------------------------------------------


def test_compute_trend_improving_with_eta():
    _reset()
    now = datetime.now(timezone.utc)
    # Seed 3 snapshots showing steady improvement: 0.83 three hours ago to
    # 0.86 now (~+0.01/hr). Target 0.95 → ETA ~9 hours out.
    with overwatch_graph._lock:
        bucket = overwatch_graph._local_store.setdefault(
            "OverwatchPlatformEvent", [])
        for i, (v, hours_ago) in enumerate([(0.83, 3), (0.85, 1.5)]):
            bucket.append({
                "id": f"snap-{i}", "event_type": "metric_snapshot",
                "service": "ci_green_rate",
                "details": json.dumps({"value": v}),
                "severity": "info",
                "created_at": (now - timedelta(hours=hours_ago)).isoformat(),
            })
    t = trend_mod.compute_trend("ci_green_rate", 0.86, lookback_hours=24)
    assert t["direction"] == "improving"
    assert t["target"] == 0.95
    assert t["projected_threshold_time"] is not None
    assert t["samples"] == 2
    assert t["rate"] > 0


def test_compute_trend_stable_when_no_history():
    _reset()
    t = trend_mod.compute_trend("ci_green_rate", 0.86)
    assert t["direction"] == "stable"
    assert t["samples"] == 0


def test_compute_trend_degrading_direction_maps_to_up_target():
    _reset()
    now = datetime.now(timezone.utc)
    with overwatch_graph._lock:
        overwatch_graph._local_store.setdefault(
            "OverwatchPlatformEvent", []).append({
                "id": "snap-x", "event_type": "metric_snapshot",
                "service": "ci_green_rate",
                "details": json.dumps({"value": 0.95}),
                "severity": "info",
                "created_at": (now - timedelta(hours=2)).isoformat(),
            })
    t = trend_mod.compute_trend("ci_green_rate", 0.86)
    assert t["direction"] == "degrading"
    assert t["projected_threshold_time"] is None


def test_record_metric_round_trips():
    _reset()
    trend_mod.record_metric("ci_green_rate", 0.88)
    rows = trend_mod.get_metric_history("ci_green_rate")
    assert len(rows) == 1
    assert rows[0][1] == 0.88


# --- 2. Timeline resolution markers -----------------------------------------


def test_supersede_marks_prior_resolved():
    _reset()
    now = datetime.now(timezone.utc).isoformat()
    overwatch_graph._local_store.setdefault(
        "OverwatchDiagnosisHistory", []).append({
            "id": "d1", "resolution_status": "ACTIVE",
            "key_findings": json.dumps(
                ["brief_leak", "synthetic_fail", "daemon_slow"]),
            "created_at": now,
        })
    resolved = tr_mod.supersede_prior_active(["daemon_slow"])
    assert set(resolved) == {"brief_leak", "synthetic_fail"}
    prior = overwatch_graph._local_store["OverwatchDiagnosisHistory"][0]
    assert prior["resolution_status"] == "SUPERSEDED"
    assert prior["resolved_at"] is not None


def test_supersede_noop_when_no_active():
    _reset()
    assert tr_mod.supersede_prior_active(["x"]) == []


def test_scheduled_diagnosis_writes_resolution_fields():
    """_record_diagnosis should write ACTIVE / RESOLVED and leave a trail."""
    _reset()
    from nexus.capabilities import scheduled_diagnosis as sd

    # First diagnosis: has findings → ACTIVE
    rec1 = sd._record_diagnosis({
        "job_id": "job-1", "confidence": 70, "status": "complete",
        "phases_completed": [{"phase": "quick_check", "findings": 2}],
    })
    assert rec1["resolution_status"] == "ACTIVE"

    # Second diagnosis: no findings → RESOLVED, and prior becomes SUPERSEDED
    rec2 = sd._record_diagnosis({
        "job_id": "job-2", "confidence": 95, "status": "complete",
        "phases_completed": [],
    })
    assert rec2["resolution_status"] == "RESOLVED"
    stored = overwatch_graph._local_store["OverwatchDiagnosisHistory"]
    prior = next(n for n in stored if n.get("diagnosis_id") == "job-1")
    assert prior["resolution_status"] == "SUPERSEDED"


# --- 3. Predictions ---------------------------------------------------------


def test_pipeline_prediction_shape():
    out = preds_mod.generate_predictions(
        tenant_data={"tenant_name": "Ben", "open_prs": 10,
                     "velocity_per_day": 2.0},
    )
    assert len(out) == 1
    p = out[0]
    assert p["category"] == "pipeline"
    assert 0 < p["confidence"] <= 1
    assert p["data"]["eta_days"] == 5.0
    assert "10 PRs open" in p["prediction"]


def test_ci_prediction_uses_trend():
    out = preds_mod.generate_predictions(
        ci_data={"trend": {
            "current": 0.86, "rate": 0.015, "target": 0.95,
            "direction": "improving",
            "projected_threshold_time": "2026-04-15T06:00:00+00:00",
        }},
    )
    ci = next(p for p in out if p["category"] == "ci")
    assert "06:00" in ci["prediction"]
    assert ci["data"]["eta"] == "2026-04-15T06:00:00+00:00"


def test_infra_prediction_skips_when_no_data():
    """Missing disk telemetry degrades gracefully instead of fabricating."""
    out = preds_mod.generate_predictions(runner_data={"runner_name": "r1"})
    assert not any(p["category"] == "infrastructure" for p in out)


def test_infra_prediction_with_data():
    out = preds_mod.generate_predictions(runner_data={
        "runner_name": "aria-runner-3",
        "disk_used_pct": 7, "disk_growth_per_day": 2.4,
    })
    infra = next(p for p in out if p["category"] == "infrastructure")
    assert "aria-runner-3" in infra["prediction"]
    assert 30 <= infra["data"]["eta_days"] <= 31


def test_format_for_report_empty():
    assert "No predictions" in preds_mod.format_for_report([])


# --- 4. Sprint context ------------------------------------------------------


def test_sprint_default_counts():
    sprint_mod.reset()
    s = sprint_mod.get_status()
    assert s["total"] == 9
    assert s["done"] == 5
    assert s["in_progress"] == 1
    assert s["not_started"] == 3
    assert "Incognito walkthrough" in s["blocker_names"]


def test_sprint_set_status_updates_counts():
    sprint_mod.reset()
    assert sprint_mod.set_item_status("incognito_walkthrough", "done") is True
    s = sprint_mod.get_status()
    assert s["done"] == 6
    assert "Incognito walkthrough" not in s["blocker_names"]


def test_sprint_invalid_status_rejected():
    sprint_mod.reset()
    import pytest
    with pytest.raises(ValueError):
        sprint_mod.set_item_status("sfs_flow", "maybe")


def test_sprint_format_for_report_has_counts_and_blockers():
    sprint_mod.reset()
    md = sprint_mod.format_for_report()
    assert "Release Readiness" in md
    assert "5/9" in md
    assert "Blockers:" in md


# --- 5. Session context -----------------------------------------------------


def test_session_context_categorizes_commits():
    commits = [
        {"sha": "a" * 40, "message": "ci(pipeline): fix runner",
         "author": "ian"},
        {"sha": "b" * 40, "message": "ux(console): add ARIA dot",
         "author": "ian"},
        {"sha": "c" * 40, "message": "fix(brief): isolation leak",
         "author": "ian"},
        {"sha": "d" * 40, "message": "random note", "author": "ian"},
    ]
    with patch(
        "nexus.forge.aria_repo.list_recent_commits",
        return_value=commits,
    ):
        ctx = sc_mod.gather_session_context(hours=24)
    assert ctx["total"] == 4
    assert ctx["counts_by_category"]["ci_cd"] == 1
    assert ctx["counts_by_category"]["ux"] == 1
    assert ctx["counts_by_category"]["brief"] == 1
    assert ctx["counts_by_category"]["other"] == 1


def test_session_context_empty_when_lookup_fails():
    with patch(
        "nexus.forge.aria_repo.list_recent_commits",
        side_effect=RuntimeError("boom"),
    ):
        ctx = sc_mod.gather_session_context()
    assert ctx["total"] == 0
    assert ctx["counts_by_category"] == {}


def test_session_context_summary_one_line():
    ctx = {"total": 12, "window_hours": 24,
           "counts_by_category": {"ux": 8, "ci_cd": 3, "infrastructure": 1}}
    s = sc_mod.summarize_one_line(ctx)
    assert "12 commits" in s
    assert "ux (8)" in s
