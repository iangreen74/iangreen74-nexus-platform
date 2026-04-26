"""Tests for Phase 0b gap-closure tool read_cloudwatch_metrics.

Spec: docs/OPERATIONAL_TRUTH_SUBSTRATE.md L145 (4th tool of §0b).
Covers happy path, extended statistics (pNN), period floor, parameter
validation, window cap + long-range opt-in, and envelope shape.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus.overwatch_v2.tools.read_tools import read_cloudwatch_metrics  # noqa: E402
from nexus.overwatch_v2.tools.read_tools.exceptions import ToolUnknown  # noqa: E402


def _patch_aws(client_mock):
    return patch("nexus.aws_client._client", return_value=client_mock)


def _dp(ts: datetime, **vals):
    return {"Timestamp": ts, "Unit": vals.pop("Unit", "Count"), **vals}


# --- Param validation ----------------------------------------------------

def test_namespace_required():
    with pytest.raises(ToolUnknown, match="required"):
        read_cloudwatch_metrics.handler(metric_name="X")


def test_metric_name_required():
    with pytest.raises(ToolUnknown, match="required"):
        read_cloudwatch_metrics.handler(namespace="X")


def test_empty_statistics_rejected():
    with pytest.raises(ToolUnknown, match="empty"):
        read_cloudwatch_metrics.handler(
            namespace="AWS/ECS", metric_name="CPUUtilization",
            statistics=[],
        )


def test_unknown_statistic_rejected():
    with pytest.raises(ToolUnknown, match="not in"):
        read_cloudwatch_metrics.handler(
            namespace="AWS/ECS", metric_name="CPUUtilization",
            statistics=["nonsense"],
        )


def test_dimensions_must_be_object():
    with pytest.raises(ToolUnknown, match="object"):
        read_cloudwatch_metrics.handler(
            namespace="AWS/ECS", metric_name="CPUUtilization",
            dimensions=["bad"],
        )


def test_end_before_start_rejected():
    with pytest.raises(ToolUnknown, match="precedes"):
        read_cloudwatch_metrics.handler(
            namespace="AWS/ECS", metric_name="CPUUtilization",
            start_time="2026-04-26T03:00:00Z",
            end_time="2026-04-26T02:00:00Z",
        )


def test_period_floor_enforced():
    m = MagicMock()
    m.get_metric_statistics.return_value = {"Datapoints": []}
    with _patch_aws(m):
        r = read_cloudwatch_metrics.handler(
            namespace="X", metric_name="Y", period=15,
        )
    assert r["period"] == 60
    assert m.get_metric_statistics.call_args.kwargs["Period"] == 60


# --- Window bounding -----------------------------------------------------

def test_window_capped_to_24h_by_default():
    m = MagicMock()
    m.get_metric_statistics.return_value = {"Datapoints": []}
    with _patch_aws(m):
        r = read_cloudwatch_metrics.handler(
            namespace="X", metric_name="Y",
            start_time="2026-04-20T00:00:00Z",
            end_time="2026-04-26T00:00:00Z",
        )
    assert r["truncated"] is True
    span = (datetime.fromisoformat(r["time_range"]["end"])
            - datetime.fromisoformat(r["time_range"]["start"]))
    assert span <= timedelta(hours=24)


def test_long_range_opt_in_extends_to_seven_days():
    m = MagicMock()
    m.get_metric_statistics.return_value = {"Datapoints": []}
    with _patch_aws(m):
        r = read_cloudwatch_metrics.handler(
            namespace="X", metric_name="Y",
            start_time="2026-04-19T00:00:00Z",
            end_time="2026-04-30T00:00:00Z",
            allow_long_range=True,
        )
    span = (datetime.fromisoformat(r["time_range"]["end"])
            - datetime.fromisoformat(r["time_range"]["start"]))
    assert span <= timedelta(days=7)
    assert r["truncated"] is True


# --- Standard statistics happy path --------------------------------------

def test_standard_statistic_average_returns_datapoints():
    t1 = datetime(2026, 4, 26, 1, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 4, 26, 1, 5, tzinfo=timezone.utc)
    m = MagicMock()
    m.get_metric_statistics.return_value = {
        "Datapoints": [
            _dp(t1, Average=42.5, SampleCount=10),
            _dp(t2, Average=50.0, SampleCount=12),
        ],
    }
    with _patch_aws(m):
        r = read_cloudwatch_metrics.handler(
            namespace="AWS/ApplicationELB",
            metric_name="HTTPCode_Target_5XX_Count",
            dimensions={"LoadBalancer": "app/overwatch-v2-alb/abc"},
            statistics=["Average"],
            period=300,
        )
    assert r["source"] == "cloudwatch_metrics"
    assert r["count"] == 2
    assert r["datapoints"][0]["statistic"] == "Average"
    assert r["datapoints"][0]["value"] == 42.5
    # API was called with Statistics, not ExtendedStatistics
    kwargs = m.get_metric_statistics.call_args.kwargs
    assert kwargs["Statistics"] == ["Average"]
    assert "ExtendedStatistics" not in kwargs


def test_multiple_standard_statistics_each_emit_a_datapoint():
    ts = datetime(2026, 4, 26, 1, 0, tzinfo=timezone.utc)
    m = MagicMock()
    m.get_metric_statistics.return_value = {
        "Datapoints": [_dp(ts, Average=10.0, Maximum=15.0, Sum=100.0, SampleCount=10)],
    }
    with _patch_aws(m):
        r = read_cloudwatch_metrics.handler(
            namespace="X", metric_name="Y",
            statistics=["Average", "Maximum", "Sum"],
        )
    stats_in_result = sorted(d["statistic"] for d in r["datapoints"])
    assert stats_in_result == ["Average", "Maximum", "Sum"]


# --- Extended statistics (pNN) -------------------------------------------

def test_extended_statistic_uses_extended_arg():
    ts = datetime(2026, 4, 26, 1, 0, tzinfo=timezone.utc)
    m = MagicMock()
    m.get_metric_statistics.return_value = {
        "Datapoints": [{
            "Timestamp": ts, "Unit": "Milliseconds",
            "ExtendedStatistics": {"p95": 123.0, "p99": 456.0},
        }],
    }
    with _patch_aws(m):
        r = read_cloudwatch_metrics.handler(
            namespace="AWS/ApplicationELB",
            metric_name="TargetResponseTime",
            statistics=["p95", "p99"],
        )
    kwargs = m.get_metric_statistics.call_args.kwargs
    assert "ExtendedStatistics" in kwargs
    assert sorted(kwargs["ExtendedStatistics"]) == ["p95", "p99"]
    assert "Statistics" not in kwargs
    by_stat = {d["statistic"]: d["value"] for d in r["datapoints"]}
    assert by_stat["p95"] == 123.0
    assert by_stat["p99"] == 456.0


def test_mixed_standard_and_extended_statistics_split_correctly():
    ts = datetime(2026, 4, 26, 1, 0, tzinfo=timezone.utc)
    m = MagicMock()
    m.get_metric_statistics.return_value = {
        "Datapoints": [{
            "Timestamp": ts, "Unit": "Milliseconds",
            "Average": 50.0,
            "ExtendedStatistics": {"p95": 100.0},
        }],
    }
    with _patch_aws(m):
        r = read_cloudwatch_metrics.handler(
            namespace="X", metric_name="Y",
            statistics=["Average", "p95"],
        )
    kwargs = m.get_metric_statistics.call_args.kwargs
    assert kwargs["Statistics"] == ["Average"]
    assert kwargs["ExtendedStatistics"] == ["p95"]
    assert r["count"] == 2


# --- Envelope shape ------------------------------------------------------

def test_envelope_includes_required_phase_0b_fields():
    m = MagicMock()
    m.get_metric_statistics.return_value = {"Datapoints": []}
    with _patch_aws(m):
        r = read_cloudwatch_metrics.handler(
            namespace="AWS/ECS", metric_name="CPUUtilization",
            dimensions={"ClusterName": "overwatch-platform"},
        )
    for k in ("source", "namespace", "metric_name", "dimensions", "period",
              "statistics", "time_range", "count", "truncated", "datapoints"):
        assert k in r, f"envelope missing {k}"
    assert r["source"] == "cloudwatch_metrics"
    assert r["dimensions"] == {"ClusterName": "overwatch-platform"}
    assert r["count"] == len(r["datapoints"])


# --- Registration --------------------------------------------------------

def test_tool_registered_with_low_risk_no_approval():
    spec = read_cloudwatch_metrics
    captured = {}

    class _ToolSpec:
        def __init__(self, **kw):
            captured.update(kw)

    fake_registry = MagicMock(
        ToolSpec=_ToolSpec, RISK_LOW="low",
        register=MagicMock(),
    )
    import sys
    with patch.dict(sys.modules,
                    {"nexus.overwatch_v2.tools.registry": fake_registry}):
        spec.register_tool()
    assert captured["name"] == "read_cloudwatch_metrics"
    assert captured["requires_approval"] is False
    assert captured["risk_level"] == "low"
