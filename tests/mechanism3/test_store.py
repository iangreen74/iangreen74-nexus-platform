"""Tests for Mechanism 3 Socratic prompt store."""
import os
from unittest.mock import MagicMock

os.environ.setdefault("NEXUS_MODE", "local")
from nexus.mechanism3.rules import SocraticPrompt
from nexus.mechanism3.store import mark_acknowledged, mark_surfaced, read_pending_prompts, save_prompts


def _prompt(**kw):
    defaults = dict(tenant_id="t1", project_id="p1", rule_name="test",
                    subject_kind="feature", subject_id="s1",
                    question="Q?", rationale="r", priority=50)
    defaults.update(kw)
    return SocraticPrompt(**defaults)


def _mock_conn():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cursor
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


def test_save_empty():
    conn, _ = _mock_conn()
    assert save_prompts([], conn) == 0


def test_save_happy():
    conn, cursor = _mock_conn()
    assert save_prompts([_prompt(), _prompt(subject_id="s2")], conn) == 2
    assert cursor.execute.call_count == 2


def test_save_continues_on_error():
    conn, cursor = _mock_conn()
    cursor.execute.side_effect = [Exception("dup"), None]
    assert save_prompts([_prompt(), _prompt(subject_id="s2")], conn) == 1


def test_read_no_conn():
    assert read_pending_prompts("t1", db_conn=None) == []


def test_read_returns_dicts():
    conn, cursor = _mock_conn()
    cursor.fetchall.return_value = [
        (1, "Q?", "r", 80, "stale_hyp", "p1"),
        (2, "Q2?", "r2", 60, "dormant", "p1"),
    ]
    result = read_pending_prompts("t1", conn, limit=5)
    assert len(result) == 2
    assert result[0]["question"] == "Q?"


def test_mark_surfaced():
    conn, cursor = _mock_conn()
    cursor.rowcount = 2
    assert mark_surfaced([1, 2], conn) == 2


def test_mark_surfaced_empty():
    conn, _ = _mock_conn()
    assert mark_surfaced([], conn) == 0


def test_mark_acknowledged():
    conn, cursor = _mock_conn()
    cursor.rowcount = 1
    assert mark_acknowledged(1, conn) is True


def test_mark_acknowledged_not_found():
    conn, cursor = _mock_conn()
    cursor.rowcount = 0
    assert mark_acknowledged(999, conn) is False
