"""AskCustomer primitive tests."""
import os
os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import patch, MagicMock, call
import pytest

from nexus.askcustomer.service import (
    AskCustomerNotConfiguredError, enqueue_ask, resolve_ask, list_pending,
)


def _mock_pg(rows=None, fetchone="UNSET"):
    """Build a mock psycopg2 connection."""
    conn = MagicMock()
    cur = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    if fetchone != "UNSET":
        cur.fetchone.return_value = fetchone
    if rows is not None:
        cur.fetchall.return_value = rows
    return conn, cur


def test_enqueue_inserts_pending_row():
    conn, cur = _mock_pg()
    with patch("nexus.askcustomer.service._pg_connect", return_value=conn), \
         patch("nexus.askcustomer.service._write_eval_event"):
        pid = enqueue_ask(
            tenant_id="t1", project_id="p1",
            question="Flask or FastAPI?",
            options=[{"value": "flask"}, {"value": "fastapi"}],
        )
    assert pid  # returns a UUID string
    cur.execute.assert_called_once()
    sql = cur.execute.call_args[0][0]
    assert "INSERT INTO ask_customer_state" in sql
    args = cur.execute.call_args[0][1]
    assert args[1] == "t1"  # tenant_id
    assert args[3] == "Flask or FastAPI?"  # question


def test_resolve_updates_and_signals_sfn():
    conn, cur = _mock_pg(
        fetchone=("t1", "p1", "Which DB?", [{"value": "pg"}], {}, "tok-123", "pending"),
    )
    mock_sfn = MagicMock()
    with patch("nexus.askcustomer.service._pg_connect", return_value=conn), \
         patch("nexus.askcustomer.service._sfn_client", return_value=mock_sfn), \
         patch("nexus.askcustomer.service._write_eval_event"):
        result = resolve_ask(
            proposal_id="pid-1", answer={"value": "pg"}, answered_by="ian",
        )
    assert result["status"] == "answered"
    mock_sfn.send_task_success.assert_called_once()
    token_arg = mock_sfn.send_task_success.call_args.kwargs["taskToken"]
    assert token_arg == "tok-123"


def test_resolve_without_task_token_skips_sfn():
    conn, cur = _mock_pg(
        fetchone=("t1", "p1", "Q?", [], {}, None, "pending"),
    )
    mock_sfn = MagicMock()
    with patch("nexus.askcustomer.service._pg_connect", return_value=conn), \
         patch("nexus.askcustomer.service._sfn_client", return_value=mock_sfn), \
         patch("nexus.askcustomer.service._write_eval_event"):
        resolve_ask(proposal_id="pid-2", answer={"v": 1}, answered_by="ian")
    mock_sfn.send_task_success.assert_not_called()


def test_resolve_not_found_raises():
    conn, cur = _mock_pg(fetchone=None)
    with patch("nexus.askcustomer.service._pg_connect", return_value=conn):
        with pytest.raises(ValueError, match="not found"):
            resolve_ask(proposal_id="missing", answer={}, answered_by="x")


def test_resolve_already_answered_raises():
    conn, cur = _mock_pg(
        fetchone=("t1", "p1", "Q", [], {}, None, "answered"),
    )
    with patch("nexus.askcustomer.service._pg_connect", return_value=conn):
        with pytest.raises(ValueError, match="answered"):
            resolve_ask(proposal_id="pid-3", answer={}, answered_by="x")


def test_list_pending_returns_rows():
    conn, cur = _mock_pg(rows=[
        ("pid-a", "Q1?", [{"v": "a"}], {}, None, None),
        ("pid-b", "Q2?", [{"v": "b"}], {}, None, None),
    ])
    with patch("nexus.askcustomer.service._pg_connect", return_value=conn):
        result = list_pending("t1")
    assert len(result) == 2
    assert result[0]["proposal_id"] == "pid-a"


def test_enqueue_writes_eval_event():
    conn, _ = _mock_pg()
    with patch("nexus.askcustomer.service._pg_connect", return_value=conn), \
         patch("nexus.askcustomer.service._write_eval_event") as mock_eval:
        enqueue_ask(tenant_id="t1", project_id="p1",
                    question="Q?", options=[])
    mock_eval.assert_called_once()
    kw = mock_eval.call_args.kwargs
    assert kw["mutation_kind"] == "enqueue"
    assert kw["tenant_id"] == "t1"
