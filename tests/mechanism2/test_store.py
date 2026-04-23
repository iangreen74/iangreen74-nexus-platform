"""Tests for Mechanism 2 deploy proposal persistence."""
import os
from unittest.mock import MagicMock, call

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.mechanism2.classifier import DeployProposal
from nexus.mechanism2.store import enqueue_proposals


def _make_proposal(**kwargs) -> DeployProposal:
    defaults = dict(
        candidate_id="c1", tenant_id="t1", project_id="p1",
        object_type="Feature", title="Test", summary="desc",
        reasoning="because", confidence=0.8, source_turn_id="ev1")
    defaults.update(kwargs)
    return DeployProposal(**defaults)


def test_empty_list_returns_zero():
    conn = MagicMock()
    assert enqueue_proposals([], conn) == 0
    conn.cursor.assert_not_called()


def test_happy_path_inserts():
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cursor
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    proposals = [_make_proposal(candidate_id="c1"),
                 _make_proposal(candidate_id="c2")]
    count = enqueue_proposals(proposals, conn)
    assert count == 2
    assert cursor.execute.call_count == 2
    conn.commit.assert_called_once()


def test_continues_past_individual_failure():
    cursor = MagicMock()
    cursor.execute.side_effect = [Exception("dup key"), None]
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cursor
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    proposals = [_make_proposal(candidate_id="c1"),
                 _make_proposal(candidate_id="c2")]
    count = enqueue_proposals(proposals, conn)
    assert count == 1  # second succeeded


def test_db_error_returns_zero_no_crash():
    conn = MagicMock()
    conn.cursor.side_effect = Exception("connection lost")
    count = enqueue_proposals([_make_proposal()], conn)
    assert count == 0
