"""Tests for nexus.operator_features.evidence_executor."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest  # noqa: E402

from nexus.operator_features import evidence_executor  # noqa: E402
from nexus.operator_features.evidence import (  # noqa: E402
    EvidenceQuery, EvidenceQueryKind, FeatureTier,
)
from nexus.operator_features.schema import OperatorFeature  # noqa: E402


def _make_query(
    kind: EvidenceQueryKind,
    spec: dict | None = None,
    section_kind: str = "table",
    name: str = "test query",
    accepts_tenant_id: bool = False,
    max_results: int = 100,
) -> EvidenceQuery:
    return EvidenceQuery(
        name=name,
        kind=kind,
        spec=spec or {},
        section_kind=section_kind,
        accepts_tenant_id=accepts_tenant_id,
        max_results=max_results,
    )


def _make_feature(queries: list[EvidenceQuery]) -> OperatorFeature:
    return OperatorFeature(
        feature_id="x", name="x", tier=FeatureTier.NICE_TO_HAVE,
        description="x", health_signals=[], evidence_queries=queries,
        falsifiability="x",
    )


# ---------------------------------------------------------------------------
# Top-level dispatch + error handling
# ---------------------------------------------------------------------------

def test_execute_evidence_queries_empty_feature():
    assert evidence_executor.execute_evidence_queries(_make_feature([])) == []


def test_unimplemented_kind_returns_error_result():
    """Stubs (CLOUDTRAIL_LOOKUP, ALB, S3, ECS_DESCRIBE) report 'not yet implemented'."""
    q = _make_query(EvidenceQueryKind.CLOUDTRAIL_LOOKUP)
    results = evidence_executor.execute_evidence_queries(_make_feature([q]))
    assert len(results) == 1
    r = results[0]
    assert r.error == "kind not yet implemented: cloudtrail_lookup"
    assert r.rows == []
    assert r.section_kind == "table"


def test_handler_exception_is_caught_and_loop_continues(monkeypatch):
    """Exception in one handler converts to error result without breaking loop."""
    q_a = _make_query(EvidenceQueryKind.NEPTUNE_CYPHER, name="will_fail",
                      spec={"cypher": "X"})
    q_b = _make_query(EvidenceQueryKind.CLOUDWATCH_LOGS, name="will_succeed",
                      spec={"log_group": "/x", "filter_pattern": ""})

    def _boom(_spec, _max, _fresh):
        raise RuntimeError("simulated graph timeout")

    monkeypatch.setitem(
        evidence_executor._QUERY_HANDLERS,
        EvidenceQueryKind.NEPTUNE_CYPHER, _boom,
    )

    fake_logs = MagicMock()
    fake_logs.filter_log_events.return_value = {"events": []}
    with patch("boto3.client", return_value=fake_logs):
        results = evidence_executor.execute_evidence_queries(
            _make_feature([q_a, q_b])
        )

    assert len(results) == 2
    by_name = {r.name: r for r in results}
    assert "RuntimeError" in (by_name["will_fail"].error or "")
    assert by_name["will_succeed"].error is None


def test_max_results_truncates_rows(monkeypatch):
    """Engine clips returned rows to query.max_results."""
    q = _make_query(EvidenceQueryKind.NEPTUNE_CYPHER, max_results=3,
                    spec={"cypher": "MATCH (n) RETURN n"})

    def _too_many(_spec, _max, _fresh):
        return [{"i": i} for i in range(10)]

    monkeypatch.setitem(
        evidence_executor._QUERY_HANDLERS,
        EvidenceQueryKind.NEPTUNE_CYPHER, _too_many,
    )
    [r] = evidence_executor.execute_evidence_queries(_make_feature([q]))
    assert r.row_count == 3
    assert r.rows == [{"i": 0}, {"i": 1}, {"i": 2}]


def test_section_kind_passes_through():
    """Engine never overrides section_kind; renderer downstream owns it."""
    q = _make_query(EvidenceQueryKind.CLOUDTRAIL_LOOKUP, section_kind="metric")
    [r] = evidence_executor.execute_evidence_queries(_make_feature([q]))
    assert r.section_kind == "metric"


# ---------------------------------------------------------------------------
# Tenant substitution
# ---------------------------------------------------------------------------

def test_tenant_substitution_when_opt_in():
    spec = {"cypher": "MATCH (t:Tenant {id: '{tenant_id}'}) RETURN t"}
    out = evidence_executor._substitute_tenant(spec, "forge-abc123")
    assert out["cypher"] == "MATCH (t:Tenant {id: 'forge-abc123'}) RETURN t"


def test_tenant_substitution_leaves_non_strings_alone():
    spec = {"cypher": "x", "limit": 50, "params": {"foo": "bar"}}
    out = evidence_executor._substitute_tenant(spec, "T1")
    assert out["limit"] == 50
    assert out["params"] == {"foo": "bar"}


def test_tenant_substitution_only_when_accepts_tenant_id_true(monkeypatch):
    """If accepts_tenant_id is False, spec strings stay literal (placeholder preserved)."""
    captured: dict = {}

    def _capture(spec, _max, _fresh):
        captured["spec"] = spec
        return []

    monkeypatch.setitem(
        evidence_executor._QUERY_HANDLERS,
        EvidenceQueryKind.NEPTUNE_CYPHER, _capture,
    )
    q = _make_query(
        EvidenceQueryKind.NEPTUNE_CYPHER,
        spec={"cypher": "MATCH (t {id: '{tenant_id}'}) RETURN t"},
        accepts_tenant_id=False,
    )
    evidence_executor.execute_evidence_queries(
        _make_feature([q]), tenant_id="forge-abc"
    )
    assert captured["spec"]["cypher"].endswith("'{tenant_id}'}) RETURN t")


# ---------------------------------------------------------------------------
# CLOUDWATCH_LOGS handler
# ---------------------------------------------------------------------------

def test_cloudwatch_logs_returns_normalised_rows():
    fake = MagicMock()
    fake.filter_log_events.return_value = {"events": [
        {"timestamp": 1745778000000, "logStreamName": "stream-a",
         "message": "hello\n"},
        {"timestamp": 1745778100000, "logStreamName": "stream-b",
         "message": "world"},
    ]}
    with patch("boto3.client", return_value=fake):
        rows = evidence_executor._exec_cloudwatch_logs(
            {"log_group": "/ecs/x", "filter_pattern": "hello"},
            max_results=100, freshness_seconds=None,
        )
    assert len(rows) == 2
    assert rows[0]["message"] == "hello"  # trailing \n stripped
    assert rows[0]["log_stream"] == "stream-a"
    assert rows[0]["timestamp"].endswith("+00:00")  # iso utc


def test_cloudwatch_logs_uses_freshness_window_when_no_spec_window():
    fake = MagicMock()
    fake.filter_log_events.return_value = {"events": []}
    with patch("boto3.client", return_value=fake):
        evidence_executor._exec_cloudwatch_logs(
            {"log_group": "/ecs/x"},  # no window_seconds in spec
            max_results=100, freshness_seconds=600,
        )
    args = fake.filter_log_events.call_args.kwargs
    actual_window = (args["endTime"] - args["startTime"]) // 1000
    assert actual_window == 600


# ---------------------------------------------------------------------------
# NEPTUNE_CYPHER handler
# ---------------------------------------------------------------------------

def test_neptune_cypher_calls_overwatch_graph(monkeypatch):
    captured: dict = {}

    def _fake_query(cypher, parameters):
        captured["cypher"] = cypher
        captured["parameters"] = parameters
        return [{"a": 1}, {"a": 2}]

    monkeypatch.setattr("nexus.overwatch_graph.query", _fake_query)
    rows = evidence_executor._exec_neptune_cypher(
        {"cypher": "MATCH (n) RETURN n", "parameters": {"x": 1}},
        max_results=100, freshness_seconds=None,
    )
    assert rows == [{"a": 1}, {"a": 2}]
    assert captured["cypher"] == "MATCH (n) RETURN n"
    assert captured["parameters"] == {"x": 1}


# ---------------------------------------------------------------------------
# POSTGRES_QUERY handler
# ---------------------------------------------------------------------------

def test_postgres_query_returns_dict_rows(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.description = [("id",), ("title",)]
    cur.fetchmany.return_value = [(1, "a"), (2, "b")]
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _fake(target):
        yield conn

    monkeypatch.setattr(
        "nexus.operator_features._pg.open_pg_connection", _fake,
    )
    rows = evidence_executor._exec_postgres_query(
        {"target": "v1", "query": "SELECT id, title FROM x"},
        max_results=100, freshness_seconds=None,
    )
    assert rows == [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]


def test_postgres_query_no_description_returns_empty(monkeypatch):
    """A statement with no result rows (e.g. analyze) returns []."""
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.description = None
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _fake(target):
        yield conn

    monkeypatch.setattr(
        "nexus.operator_features._pg.open_pg_connection", _fake,
    )
    rows = evidence_executor._exec_postgres_query(
        {"query": "ANALYZE"}, max_results=10, freshness_seconds=None,
    )
    assert rows == []


def test_row_to_jsonable_stringifies_complex_types():
    from datetime import datetime
    row = (1, "x", None, True, datetime(2026, 4, 27, 18, 0))
    out = evidence_executor._row_to_jsonable(row)
    assert out[:4] == (1, "x", None, True)
    assert isinstance(out[4], str)


# ---------------------------------------------------------------------------
# End-to-end via top-level
# ---------------------------------------------------------------------------

def test_top_level_round_trip(monkeypatch):
    """Execute a full feature with one query of each implemented kind."""
    q1 = _make_query(EvidenceQueryKind.CLOUDWATCH_LOGS, name="q1",
                     spec={"log_group": "/x"}, section_kind="list")
    q2 = _make_query(EvidenceQueryKind.NEPTUNE_CYPHER, name="q2",
                     spec={"cypher": "RETURN 1"}, section_kind="metric")
    q3 = _make_query(EvidenceQueryKind.S3_LISTING, name="q3-stub",
                     section_kind="text")

    fake_logs = MagicMock()
    fake_logs.filter_log_events.return_value = {"events": [
        {"timestamp": 1745778000000, "message": "x", "logStreamName": "s"}
    ]}
    monkeypatch.setattr("boto3.client", lambda *a, **kw: fake_logs)
    monkeypatch.setattr("nexus.overwatch_graph.query",
                        lambda cypher, params: [{"v": 1}])

    results = evidence_executor.execute_evidence_queries(
        _make_feature([q1, q2, q3])
    )
    assert len(results) == 3
    by_name = {r.name: r for r in results}
    assert by_name["q1"].row_count == 1
    assert by_name["q1"].section_kind == "list"
    assert by_name["q2"].rows == [{"v": 1}]
    assert by_name["q2"].section_kind == "metric"
    assert by_name["q3-stub"].error is not None  # stub kind
    assert by_name["q3-stub"].section_kind == "text"
