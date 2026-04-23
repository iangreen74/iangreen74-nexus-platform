"""Tests for Mechanism 3 Socratic prompt rules."""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

os.environ.setdefault("NEXUS_MODE", "local")
from nexus.mechanism3.rules import SocraticPrompt, _fmtdate, _trunc, scan_tenant


def _mock_graph(results=None):
    g = MagicMock()
    g.query.return_value = results or []
    return g


def test_stale_hyp_no_data():
    assert scan_tenant("t1", graph=_mock_graph([])) == []


def test_stale_hyp_produces_prompt():
    g = MagicMock()
    g.query.side_effect = lambda q, params=None: (
        [{"id": "h1", "stmt": "Users prefer dark mode", "pid": "p1"}]
        if "Hypothesis" in q else [])
    prompts = scan_tenant("t1", graph=g)
    hyp = [p for p in prompts if p.rule_name == "stale_hypothesis"]
    assert len(hyp) == 1
    assert "dark mode" in hyp[0].question


def test_stale_hyp_no_stmt_skipped():
    g = MagicMock()
    g.query.side_effect = lambda q, params=None: (
        [{"id": "h1", "stmt": "", "pid": "p1"}]
        if "Hypothesis" in q else [])
    assert not any(p.rule_name == "stale_hypothesis" for p in scan_tenant("t1", graph=g))


def test_dormant_decision_produces():
    g = MagicMock()
    old = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
    g.query.side_effect = lambda q, params=None: (
        [{"id": "d1", "name": "Use PostgreSQL", "upd": old, "pid": "p1"}]
        if "Decision" in q else [])
    dec = [p for p in scan_tenant("t1", graph=g) if p.rule_name == "dormant_decision"]
    assert len(dec) == 1
    assert "PostgreSQL" in dec[0].question


def test_built_not_deployed_no_db():
    g = MagicMock()
    g.query.return_value = [{"id": "f1", "name": "Auth", "pid": "p1"}]
    assert not any(p.rule_name == "built_not_deployed"
                   for p in scan_tenant("t1", graph=g, db_conn=None))


def test_built_not_deployed_with_feature():
    g = MagicMock()
    g.query.side_effect = lambda q, params=None: (
        [{"id": "f1", "name": "Auth flow", "pid": "p1"}]
        if "Feature" in q else [])
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cursor
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    built = [p for p in scan_tenant("t1", graph=g, db_conn=conn)
             if p.rule_name == "built_not_deployed"]
    assert len(built) == 1
    assert "Auth flow" in built[0].question


def test_failure_streak_no_db():
    assert not any(p.rule_name == "deploy_failure_streak"
                   for p in scan_tenant("t1", graph=_mock_graph(), db_conn=None))


def test_failure_streak_3():
    g = _mock_graph()
    cursor = MagicMock()
    cursor.fetchall.return_value = [("proj-x", 4)]
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cursor
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    streaks = [p for p in scan_tenant("t1", graph=g, db_conn=conn)
               if p.rule_name == "deploy_failure_streak"]
    assert len(streaks) == 1
    assert "4" in streaks[0].question


def test_scan_aggregates():
    g = MagicMock()
    g.query.side_effect = lambda q, params=None: (
        [{"id": "h1", "stmt": "Test", "pid": "p1"}] if "Hypothesis" in q else
        [{"id": "d1", "name": "Dec", "upd": "2026-01-01T00:00:00Z", "pid": "p1"}]
        if "Decision" in q else [])
    rules = {p.rule_name for p in scan_tenant("t1", graph=g)}
    assert "stale_hypothesis" in rules
    assert "dormant_decision" in rules


def test_rule_error_continues():
    g = MagicMock()
    g.query.side_effect = Exception("down")
    assert scan_tenant("t1", graph=g) == []


def test_trunc():
    assert _trunc("hello", 10) == "hello"
    assert len(_trunc("hello world foo", 10)) <= 10


def test_fmtdate():
    assert _fmtdate("2026-03-15T10:00:00Z") == "Mar 15"
    assert _fmtdate("garbage") == "recently"
