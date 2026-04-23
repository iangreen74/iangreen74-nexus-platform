"""Tests for rolling summaries — Phase 6."""
import json
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.summaries.generator import (
    _structural_fallback,
    generate_daily_digest,
    generate_monthly_arc,
    generate_weekly_rollup,
)
from nexus.summaries.store import (
    read_past_digests,
    read_summaries,
    save_summary,
)


# --- Generator tests ----------------------------------------------------------


def test_generate_daily_no_activity_returns_fallback():
    """In local mode (no Haiku), returns structural fallback."""
    result = generate_daily_digest("forge-test")
    assert isinstance(result, str)
    assert len(result) > 0
    assert "summary" in result.lower() or "auto-generated" in result.lower()


def test_generate_daily_mock_haiku_happy_path(monkeypatch):
    monkeypatch.setattr(
        "nexus.summaries.generator._invoke_haiku",
        lambda p: "You spent the day building auth. Tone: focused.",
    )
    result = generate_daily_digest("forge-test")
    assert "auth" in result


def test_generate_daily_haiku_error_returns_fallback(monkeypatch):
    monkeypatch.setattr(
        "nexus.summaries.generator._invoke_haiku", lambda p: None,
    )
    result = generate_daily_digest("forge-test")
    assert len(result) > 0
    assert "auto-generated" in result.lower()


def test_generate_weekly_reads_daily_digests(monkeypatch):
    import nexus.summaries.store as _store
    monkeypatch.setattr(
        _store, "read_past_digests",
        lambda tid, h, limit=7: [
            {"text": "Monday: worked on auth", "for_date": "2026-04-21"},
            {"text": "Tuesday: deployed to prod", "for_date": "2026-04-22"},
        ],
    )
    monkeypatch.setattr(
        "nexus.summaries.generator._invoke_haiku",
        lambda p: "This week was about shipping auth to production.",
    )
    result = generate_weekly_rollup("forge-test")
    assert "auth" in result


def test_generate_weekly_no_dailies_returns_fallback(monkeypatch):
    import nexus.summaries.store as _store
    monkeypatch.setattr(
        _store, "read_past_digests", lambda tid, h, limit=7: [],
    )
    result = generate_weekly_rollup("forge-test")
    assert "auto-generated" in result.lower()


def test_generate_monthly_reads_weekly_rollups(monkeypatch):
    import nexus.summaries.store as _store
    monkeypatch.setattr(
        _store, "read_past_digests",
        lambda tid, h, limit=5: [
            {"text": "Week 1: foundation laid", "for_date": "2026-04-07"},
            {"text": "Week 2: auth shipped", "for_date": "2026-04-14"},
        ],
    )
    monkeypatch.setattr(
        "nexus.summaries.generator._invoke_haiku",
        lambda p: "April was the month you stopped building alone.",
    )
    result = generate_monthly_arc("forge-test")
    assert "alone" in result or "April" in result


def test_structural_fallback_never_empty():
    result = _structural_fallback("forge-test", "daily", "line1\nline2\nline3")
    assert len(result) > 0
    assert "3" in result  # 3 lines counted


def test_daily_digest_includes_tone_data(monkeypatch):
    """Verifies tone markers are fed to the Haiku prompt."""
    captured = {}

    def _capture_haiku(prompt):
        captured["prompt"] = prompt
        return "Summary with tone."

    monkeypatch.setattr(
        "nexus.summaries.generator._invoke_haiku", _capture_haiku,
    )
    monkeypatch.setattr(
        "nexus.summaries.generator._read_tone_summary",
        lambda tid: "anxious, curious, excited",
    )
    generate_daily_digest("forge-test")
    assert "anxious" in captured.get("prompt", "")


# --- Store tests --------------------------------------------------------------


def test_save_summary_happy_path(monkeypatch):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    monkeypatch.setattr("nexus.summaries.store._pg_connect", lambda: mock_conn)

    result = save_summary("t-1", "daily", "Test summary", date(2026, 4, 23))
    assert result is True
    mock_cursor.execute.assert_called_once()
    sql = mock_cursor.execute.call_args[0][0]
    assert "ON CONFLICT" in sql


def test_read_summaries_returns_latest(monkeypatch):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    def _fetchone_side_effect():
        # Returns result for each horizon query
        return ("Test daily summary",)

    mock_cursor.fetchone = _fetchone_side_effect
    monkeypatch.setattr("nexus.summaries.store._pg_connect", lambda: mock_conn)

    result = read_summaries("t-1")
    assert result["daily"] == "Test daily summary"
    assert result["weekly"] == "Test daily summary"
    assert result["monthly"] == "Test daily summary"


def test_read_summaries_empty_returns_all_none(monkeypatch):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None
    monkeypatch.setattr("nexus.summaries.store._pg_connect", lambda: mock_conn)

    result = read_summaries("t-1")
    assert result == {"daily": None, "weekly": None, "monthly": None}


def test_read_summaries_db_error_returns_all_none(monkeypatch):
    from nexus.summaries.store import SummaryStoreNotConfiguredError
    monkeypatch.setattr(
        "nexus.summaries.store._pg_connect",
        lambda: (_ for _ in ()).throw(SummaryStoreNotConfiguredError("no db")),
    )
    result = read_summaries("t-1")
    assert result == {"daily": None, "weekly": None, "monthly": None}


def test_read_past_digests_returns_list(monkeypatch):
    now = datetime.now(timezone.utc)
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        ("Day 1 summary", date(2026, 4, 22), now),
        ("Day 2 summary", date(2026, 4, 23), now),
    ]
    monkeypatch.setattr("nexus.summaries.store._pg_connect", lambda: mock_conn)

    result = read_past_digests("t-1", "daily", limit=7)
    assert len(result) == 2
    assert result[0]["text"] == "Day 1 summary"
    assert result[0]["for_date"] == "2026-04-22"


# --- Ontology reader integration ---------------------------------------------


def test_read_rolling_summaries_wired(monkeypatch):
    """ontology_reader.read_rolling_summaries calls store.read_summaries."""
    monkeypatch.setattr(
        "nexus.summaries.store._pg_connect",
        lambda: (_ for _ in ()).throw(
            __import__("nexus.summaries.store", fromlist=["SummaryStoreNotConfiguredError"])
            .SummaryStoreNotConfiguredError("no db")
        ),
    )
    from nexus.aria.ontology_reader import read_rolling_summaries
    result = read_rolling_summaries("forge-test")
    assert result == {"daily": None, "weekly": None, "monthly": None}
