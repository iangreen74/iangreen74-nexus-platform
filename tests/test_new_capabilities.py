"""Tests for the 5 new Overwatch capabilities."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import (  # noqa: E402
    bedrock_monitor as bm,
    cost_monitor as cm,
    neptune_integrity as ni,
    onboarding_monitor as om,
    platform_healer as ph,
)


def _reset():
    overwatch_graph.reset_local_store()
    ph.reset_state()


# --- Neptune integrity ------------------------------------------------------


def test_integrity_local_mode_clean():
    _reset()
    report = ni.run_integrity_scan()
    assert report["healthy"] is True
    assert report["finding_count"] == 0


def test_integrity_journey_skips_in_local():
    _reset()
    assert ni.journey_neptune_integrity()["status"] == "skip"


def test_integrity_production_detects_orphans():
    _reset()
    def fake_run_count(q):
        if "MissionTask" in q: return 12
        if "BriefEntry" in q: return 3
        return 0
    with patch("nexus.capabilities.neptune_integrity.MODE", "production"), \
         patch("nexus.capabilities.neptune_integrity._run_count",
               side_effect=fake_run_count):
        report = ni.run_integrity_scan()
    assert report["healthy"] is False
    names = {f["name"] for f in report["findings"]}
    assert "orphan_mission_tasks" in names
    assert "orphan_brief_entries" in names


def test_auto_repair_skips_without_label():
    out = ni.auto_repair({"name": "dangling_imports_edges",
                          "auto_repair_label": None})
    assert out["action"] == "skipped"


def test_auto_repair_dry_run_reports_count():
    _reset()
    def fake_query(q, params=None):
        if "count(n)" in q: return [{"cnt": 42}]
        return []
    with patch("nexus.capabilities.neptune_integrity.MODE", "production"), \
         patch("nexus.capabilities.neptune_integrity.overwatch_graph.query",
               side_effect=fake_query):
        out = ni.auto_repair({"name": "orphan_mission_tasks",
                              "auto_repair_label": "MissionTask"},
                             dry_run=True)
    assert out["action"] == "dry_run"
    assert out["would_delete"] == 42


# --- Cost monitor -----------------------------------------------------------


def test_cost_local_mode_mock():
    s = cm.get_daily_spend()
    assert s["mock"] is True
    assert s["today"] == 31.42


def test_cost_production_parses_ce_response():
    ce = MagicMock()
    day_resp = {"ResultsByTime": [
        {"Metrics": {"UnblendedCost": {"Amount": "10.00"}}}
    ]}
    mtd_resp = {"ResultsByTime": [{
        "Groups": [
            {"Keys": ["Amazon EC2"],
             "Metrics": {"UnblendedCost": {"Amount": "100.00"}}},
            {"Keys": ["Amazon Neptune"],
             "Metrics": {"UnblendedCost": {"Amount": "40.00"}}},
        ],
    }]}
    # Order of CE calls: today, yesterday, mtd.
    ce.get_cost_and_usage.side_effect = [day_resp, day_resp, mtd_resp]
    with patch("nexus.capabilities.cost_monitor.MODE", "production"), \
         patch("nexus.capabilities.cost_monitor._ce_client", return_value=ce):
        s = cm.get_daily_spend()
    assert s["today"] == 10.0
    assert s["month_to_date"] == 140.0
    assert s["top_services"][0]["service"] == "Amazon EC2"
    assert s["burn_rate_per_day"] > 0


def test_cost_journey_skip_local():
    assert cm.journey_cost_monitoring()["status"] == "skip"


def test_cost_format_for_report_handles_error():
    md = cm.format_for_report({"error": "AccessDenied"})
    assert "AccessDenied" in md


# --- Onboarding monitor -----------------------------------------------------


def test_onboarding_local_mode_mock():
    s = om.get_onboarding_status("forge-xxx")
    assert s["mock"] is True
    assert s["current_stage"] == "brief_generated"


def test_onboarding_format_no_stalls():
    md = om.format_for_report({"tenants": [{"tenant_id": "x"}],
                                "stalled": []})
    assert "none stalled" in md


def test_onboarding_format_lists_stalled():
    md = om.format_for_report({
        "tenants": [{"tenant_id": "forge-abc"}],
        "stalled": [{
            "tenant_id": "forge-abc", "current_stage": "brief_generated",
            "time_in_current_stage": "3h", "stall_threshold": "15m",
            "hint": "Bedrock may be down",
        }],
    })
    assert "forge-abc" in md
    assert "brief_generated" in md
    assert "Bedrock may be down" in md


def test_onboarding_fmt_duration():
    assert om._fmt_duration(30) == "30s"
    assert om._fmt_duration(120) == "2m"
    assert om._fmt_duration(3720) == "1h 2m"


# --- Bedrock monitor --------------------------------------------------------


def test_bedrock_local_mode_mock():
    m = bm.get_bedrock_metrics()
    assert m["mock"] is True
    assert m["invocations"] == 342


def test_bedrock_journey_skip_local():
    assert bm.journey_bedrock_health()["status"] == "skip"


def test_bedrock_estimate_cost():
    # Sonnet: 1M input @ $3/M, 500K output @ $15/M → $3 + $7.50 = $10.50
    assert bm._estimate_cost(1_000_000, 500_000, "sonnet") == 10.50


def test_bedrock_format_for_report_error():
    md = bm.format_for_report({"error": "AccessDenied"})
    assert "AccessDenied" in md


def test_bedrock_production_aggregates():
    datapoints = {
        "Invocations": 500, "InputTokenCount": 900_000,
        "OutputTokenCount": 200_000, "InvocationClientErrors": 2,
        "InvocationServerErrors": 1,
    }
    def fake_sum(metric, start, end, period_sec=3600):
        return datapoints.get(metric, 0)
    def fake_lat(start, end):
        return {"p50": 1.2, "p90": 3.4, "p99": 8.0}
    with patch("nexus.capabilities.bedrock_monitor.MODE", "production"), \
         patch("nexus.capabilities.bedrock_monitor._sum_metric",
               side_effect=fake_sum), \
         patch("nexus.capabilities.bedrock_monitor._latency_pct",
               side_effect=fake_lat):
        m = bm.get_bedrock_metrics(hours=24)
    assert m["invocations"] == 500
    assert m["errors"] == 3
    assert m["latency"]["p99"] == 8.0
    assert m["estimated_cost"] > 0


# --- Platform healer --------------------------------------------------------


def test_healer_local_detections_all_false():
    """Local mode → every detection returns False."""
    for fn in (ph._check_daemon_stale, ph._check_neptune_slow,
               ph._check_api_unhealthy, ph._check_placeholder_noise):
        assert fn() is False


def test_healer_operational_synthetic_passes():
    out = ph.journey_healer_operational()
    assert out["status"] == "pass"
    assert "4 detections OK" in out["details"]


def test_healer_chain_triggers_advances_resolves():
    _reset()
    # HEAL_CHAINS captures callables at import; patch the detection slot
    # directly rather than the module-level function so evaluate sees it.
    stale = [True]
    ph.HEAL_CHAINS["daemon_stale"]["detection"] = lambda: stale[0]
    try:
        r1 = ph.evaluate_heal_chains()
        assert "daemon_stale" in r1["triggered"]
        assert ph.get_active_chains().get("daemon_stale", {}).get("step") == 1
        r2 = ph.evaluate_heal_chains()
        assert ph.get_active_chains().get("daemon_stale", {}).get("step") == 2
        stale[0] = False
        r3 = ph.evaluate_heal_chains()
        assert any(s.get("outcome") == "resolved" for s in r3["steps"])
        assert "daemon_stale" not in ph.get_active_chains()
    finally:
        ph.HEAL_CHAINS["daemon_stale"]["detection"] = ph._check_daemon_stale


def test_healer_execute_step_unknown_chain():
    out = ph.execute_step("nope", 0)
    assert out["ok"] is False
    assert out["reason"] == "unknown_chain"
