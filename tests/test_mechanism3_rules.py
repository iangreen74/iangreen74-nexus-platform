"""Tests for nexus/mechanism3/rules.py — Socratic rule evaluation.

Focus is the post-migration-012 contract: rules no longer swallow
schema-drift errors silently. _deploy_failure_streak and
_recent_success_pids let real DB errors propagate to scan_tenant()'s
outer try/except, and emit an observable warning when the missing
mechanism2 producer is the reason for zero results.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from nexus.mechanism3.rules import (
    _deploy_failure_streak,
    _recent_success_pids,
    scan_tenant,
)


def _mock_conn(rows: list = None):
    """Build a psycopg2-style connection that returns ``rows`` from fetchall."""
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = rows or []
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_deploy_failure_streak_warns_on_missing_producer(caplog):
    """The rule logs a warning making the unbuilt-mechanism2 state observable
    in CloudWatch — replaces the prior silent ``except Exception: return []``."""
    conn, _ = _mock_conn(rows=[])
    with caplog.at_level(logging.WARNING, logger="nexus.mechanism3.rules"):
        result = _deploy_failure_streak("tid-1", graph=None, db_conn=conn)
    assert result == []
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "deploy_failure_streak" in msgs
    assert "mechanism2 is unbuilt" in msgs
    assert "SILENT_EXCEPT_SWEEP" in msgs


def test_deploy_failure_streak_returns_empty_when_no_db():
    """Short-circuit on missing db_conn stays — no DB call, no log noise."""
    assert _deploy_failure_streak("tid-1", graph=None, db_conn=None) == []


def test_deploy_failure_streak_propagates_db_errors():
    """Real DB errors must propagate. Prior silent except hid schema drift
    and would now hide all future DB errors. scan_tenant()'s outer
    try/except logs the failure with rule context."""
    conn = MagicMock()
    conn.cursor.side_effect = RuntimeError("simulated DB failure")
    with pytest.raises(RuntimeError, match="simulated DB failure"):
        _deploy_failure_streak("tid-1", graph=None, db_conn=conn)


def test_recent_success_pids_propagates_db_errors():
    """Same contract: real DB errors propagate; outer scan_tenant() handles."""
    conn = MagicMock()
    conn.cursor.side_effect = RuntimeError("simulated DB failure")
    with pytest.raises(RuntimeError, match="simulated DB failure"):
        _recent_success_pids("tid-1", ["pid-1"], conn)


def test_recent_success_pids_short_circuits_on_no_pids():
    """No active project ids → no DB call → empty set."""
    conn = MagicMock()
    assert _recent_success_pids("tid-1", [], conn) == set()
    conn.cursor.assert_not_called()


def test_scan_tenant_isolates_rule_failures(caplog):
    """When a rule raises, scan_tenant logs and continues — other rules
    still run. This is the orchestration-level safety net that replaces
    the per-rule silent excepts."""
    # Mock graph so non-deploy rules don't try real Neptune
    graph = MagicMock()
    graph.query.return_value = []
    # db_conn that explodes
    conn = MagicMock()
    conn.cursor.side_effect = RuntimeError("boom")
    with caplog.at_level(logging.WARNING, logger="nexus.mechanism3.rules"):
        prompts = scan_tenant("tid-1", graph=graph, db_conn=conn)
    assert prompts == []
    msgs = " ".join(r.getMessage() for r in caplog.records)
    # The failing rules' names must appear in the outer warning log
    assert "_deploy_failure_streak failed" in msgs or "_built_not_deployed failed" in msgs
