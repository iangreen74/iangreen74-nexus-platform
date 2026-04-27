"""Tests for nexus.operator_features.signal_evaluator."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest  # noqa: E402

from nexus.operator_features import signal_evaluator  # noqa: E402
from nexus.operator_features.evidence import FeatureTier  # noqa: E402
from nexus.operator_features.schema import OperatorFeature  # noqa: E402
from nexus.operator_features.signals import (  # noqa: E402
    HealthSignal, SignalQueryKind, SignalStatus,
)


def _make_signal(
    name: str = "test_sig",
    query_kind: SignalQueryKind = SignalQueryKind.CLOUDWATCH_METRIC,
    query_spec: dict | None = None,
    green: float = 95.0,
    amber: float = 80.0,
    comparison: str = "gte",
    unit: str = "percent",
) -> HealthSignal:
    return HealthSignal(
        name=name, description="x",
        query_kind=query_kind,
        query_spec=query_spec or {},
        unit=unit,
        green_threshold=green,
        amber_threshold=amber,
        comparison=comparison,
    )


def _make_feature(signals: list[HealthSignal]) -> OperatorFeature:
    return OperatorFeature(
        feature_id="x", name="x", tier=FeatureTier.NICE_TO_HAVE,
        description="x", health_signals=signals,
        evidence_queries=[], falsifiability="x",
    )


# ---------------------------------------------------------------------------
# Top-level + error-wrapping
# ---------------------------------------------------------------------------

def test_evaluate_health_signals_empty_feature():
    assert signal_evaluator.evaluate_health_signals(_make_feature([])) == []


def test_evaluate_health_signals_uses_status_for(monkeypatch):
    """Engine must defer to signal.status_for() — not reimplement thresholds."""
    sig = _make_signal(green=95.0, amber=80.0, comparison="gte")
    feat = _make_feature([sig])

    # Force a known value; status_for(98.0) should return GREEN.
    monkeypatch.setattr(signal_evaluator, "_query_value", lambda s: 98.0)

    results = signal_evaluator.evaluate_health_signals(feat)
    assert len(results) == 1
    r = results[0]
    assert r.status == SignalStatus.GREEN
    assert r.observed_value == 98.0
    assert "GREEN >= 95.0" in r.threshold_summary
    assert "98.0 percent (GREEN)" == r.detail


def test_evaluate_health_signals_none_value_is_unknown(monkeypatch):
    """status_for(None) → UNKNOWN; engine must propagate."""
    sig = _make_signal()
    monkeypatch.setattr(signal_evaluator, "_query_value", lambda s: None)
    results = signal_evaluator.evaluate_health_signals(_make_feature([sig]))
    assert results[0].status == SignalStatus.UNKNOWN
    assert results[0].observed_value is None


def test_evaluate_health_signals_handler_exception_is_unknown(monkeypatch):
    """Exception in _query_value → UNKNOWN with detail; loop continues."""
    sig_a = _make_signal(name="will_fail")
    sig_b = _make_signal(name="will_succeed")

    def _fake_query(signal):
        if signal.name == "will_fail":
            raise RuntimeError("simulated boto3 throttle")
        return 99.0

    monkeypatch.setattr(signal_evaluator, "_query_value", _fake_query)
    results = signal_evaluator.evaluate_health_signals(
        _make_feature([sig_a, sig_b])
    )
    assert len(results) == 2
    by_name = {r.name: r for r in results}
    assert by_name["will_fail"].status == SignalStatus.UNKNOWN
    assert "RuntimeError" in by_name["will_fail"].detail
    assert by_name["will_succeed"].status == SignalStatus.GREEN


def test_unknown_query_kind_returns_none_value(monkeypatch):
    """A kind not in _VALUE_HANDLERS (e.g. NEPTUNE_COUNT today) → None → UNKNOWN."""
    sig = _make_signal(query_kind=SignalQueryKind.NEPTUNE_COUNT,
                       query_spec={"cypher": "MATCH (n) RETURN count(n)"})
    results = signal_evaluator.evaluate_health_signals(_make_feature([sig]))
    assert results[0].observed_value is None
    assert results[0].status == SignalStatus.UNKNOWN


# ---------------------------------------------------------------------------
# CLOUDWATCH_METRIC handler
# ---------------------------------------------------------------------------

def test_cloudwatch_metric_averages_datapoints():
    fake = MagicMock()
    fake.get_metric_statistics.return_value = {"Datapoints": [
        {"Average": 100.0}, {"Average": 90.0}, {"Average": 80.0},
    ]}
    with patch("boto3.client", return_value=fake):
        v = signal_evaluator._eval_cloudwatch_metric({
            "namespace": "AWS/ECS",
            "metric_name": "CPUUtilization",
            "dimensions": [{"Name": "ClusterName", "Value": "x"}],
        })
    assert v == 90.0
    args = fake.get_metric_statistics.call_args.kwargs
    assert args["Namespace"] == "AWS/ECS"
    assert args["Statistics"] == ["Average"]
    assert args["Period"] == 60


def test_cloudwatch_metric_no_datapoints_returns_none():
    fake = MagicMock()
    fake.get_metric_statistics.return_value = {"Datapoints": []}
    with patch("boto3.client", return_value=fake):
        v = signal_evaluator._eval_cloudwatch_metric({
            "namespace": "AWS/ECS", "metric_name": "x",
        })
    assert v is None


def test_cloudwatch_metric_respects_custom_statistic():
    fake = MagicMock()
    fake.get_metric_statistics.return_value = {"Datapoints": [
        {"Sum": 12.0}, {"Sum": 8.0},
    ]}
    with patch("boto3.client", return_value=fake):
        v = signal_evaluator._eval_cloudwatch_metric({
            "namespace": "AWS/ECS",
            "metric_name": "ErrorCount",
            "statistic": "Sum",
        })
    assert v == 10.0
    args = fake.get_metric_statistics.call_args.kwargs
    assert args["Statistics"] == ["Sum"]


# ---------------------------------------------------------------------------
# CLOUDWATCH_LOG_COUNT handler
# ---------------------------------------------------------------------------

def test_log_count_returns_event_count():
    fake = MagicMock()
    fake.filter_log_events.return_value = {
        "events": [{"message": "x"}, {"message": "y"}, {"message": "z"}]
    }
    with patch("boto3.client", return_value=fake):
        v = signal_evaluator._eval_cloudwatch_log_count({
            "log_group": "/ecs/forgescaler",
            "filter_pattern": "ERROR",
        })
    assert v == 3.0
    args = fake.filter_log_events.call_args.kwargs
    assert args["logGroupName"] == "/ecs/forgescaler"
    assert args["filterPattern"] == "ERROR"


def test_log_count_zero_when_no_events():
    fake = MagicMock()
    fake.filter_log_events.return_value = {"events": []}
    with patch("boto3.client", return_value=fake):
        v = signal_evaluator._eval_cloudwatch_log_count({
            "log_group": "/ecs/x", "filter_pattern": "x",
        })
    assert v == 0.0


# ---------------------------------------------------------------------------
# POSTGRES_QUERY handler
# ---------------------------------------------------------------------------

def test_postgres_query_returns_scalar(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.fetchone.return_value = (42,)
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _fake_conn(target):
        yield conn

    monkeypatch.setattr(signal_evaluator, "_open_pg_connection", _fake_conn)
    v = signal_evaluator._eval_postgres_query({
        "target": "v1", "query": "SELECT count(*) FROM classifier_proposals",
    })
    assert v == 42.0
    cur.execute.assert_called_once_with(
        "SELECT count(*) FROM classifier_proposals"
    )


def test_postgres_query_null_row_returns_none(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _fake_conn(target):
        yield conn

    monkeypatch.setattr(signal_evaluator, "_open_pg_connection", _fake_conn)
    v = signal_evaluator._eval_postgres_query({"query": "SELECT 1"})
    assert v is None


def test_postgres_query_unknown_target_raises():
    with pytest.raises(ValueError, match="unknown postgres target"):
        with signal_evaluator._open_pg_connection("v3"):
            pass


def test_postgres_query_v1_missing_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cm = signal_evaluator._open_pg_connection("v1")
    with pytest.raises(RuntimeError, match="DATABASE_URL not set"):
        with cm:
            pass


# ---------------------------------------------------------------------------
# Threshold formatting
# ---------------------------------------------------------------------------

def test_format_threshold_gte():
    sig = _make_signal(green=95.0, amber=80.0, comparison="gte", unit="percent")
    assert signal_evaluator._format_threshold(sig) == (
        "GREEN >= 95.0 percent, AMBER >= 80.0 percent"
    )


def test_format_threshold_lte():
    sig = _make_signal(green=1.0, amber=5.0, comparison="lte", unit="seconds")
    assert signal_evaluator._format_threshold(sig) == (
        "GREEN <= 1.0 seconds, AMBER <= 5.0 seconds"
    )
