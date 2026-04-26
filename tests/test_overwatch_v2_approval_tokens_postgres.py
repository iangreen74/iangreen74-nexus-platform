"""Tests for the production (Postgres) path of approval_tokens.

The code path is gated on `MODE == "production"`; tests flip the module-level
MODE constant per-test and patch nexus.overwatch_v2.db.get_conn so
no real DB connection is needed. Behavioural equivalence between the
in-memory branch (MODE=local) and the Postgres branch (MODE=production)
is asserted explicitly — a regression in either should fail at least
one of these tests."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from nexus.overwatch_v2.auth import approval_tokens as at


# ---- Fake DB --------------------------------------------------------------

class _FakeRows:
    """Tiny in-memory stand-in for the approval_tokens table.

    Models exactly the two operations approval_tokens code performs:
      INSERT INTO approval_tokens (...)
      UPDATE approval_tokens SET used=true ... WHERE token_id=%s AND used=false
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple) -> None:
        self.executed.append((sql, params))
        if sql.lstrip().upper().startswith("INSERT INTO APPROVAL_TOKENS"):
            (token_id, proposal_id, proposal_hash, issued_at, expires_at,
             issuer) = params
            if token_id in self.rows:
                raise RuntimeError("duplicate token_id (PK violation)")
            self.rows[token_id] = {
                "token_id": token_id, "proposal_id": proposal_id,
                "proposal_hash": proposal_hash, "issued_at": issued_at,
                "expires_at": expires_at, "issuer": issuer, "used": False,
            }
            self._fetch = None
        elif sql.lstrip().upper().startswith("UPDATE APPROVAL_TOKENS"):
            (token_id,) = params
            row = self.rows.get(token_id)
            if row is None or row["used"]:
                self._fetch = None
                return
            row["used"] = True
            self._fetch = (token_id,)
        else:
            self._fetch = None

    def fetchone(self):
        return getattr(self, "_fetch", None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_db(monkeypatch):
    table = _FakeRows()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = table

    @contextmanager
    def _fake_get_conn():
        yield fake_conn

    import nexus.overwatch_v2.db as db_module
    monkeypatch.setattr(db_module, "get_conn", _fake_get_conn)
    return table


@pytest.fixture
def production_mode(monkeypatch):
    monkeypatch.setattr(at, "MODE", "production")
    yield
    at._reset_for_tests()


@pytest.fixture(autouse=True)
def _reset():
    at._reset_for_tests()
    yield
    at._reset_for_tests()


# ---- _record_issue --------------------------------------------------------

def test_record_issue_writes_all_seven_columns(production_mode, fake_db):
    """proposal_hash and issuer must actually land — that's the gap that
    blocked Phase 1's end-to-end verification."""
    at.issue_token(
        proposal_id="tool:comment_on_pr",
        proposal_payload={"tool_name": "comment_on_pr", "params": {"k": "v"}},
        issuer="ian@vaultscaler.com",
        ttl_seconds=60,
    )
    inserts = [(s, p) for s, p in fake_db.executed
               if s.lstrip().upper().startswith("INSERT INTO APPROVAL_TOKENS")]
    assert len(inserts) == 1
    sql, params = inserts[0]
    # All seven columns are explicitly enumerated.
    for col in ("token_id", "proposal_id", "proposal_hash",
                "issued_at", "expires_at", "issuer", "used"):
        assert col in sql, f"INSERT missing column {col!r}: {sql}"
    assert len(params) == 6, "INSERT supplies 6 params; `used=false` is literal"


def test_record_issue_uses_unprefixed_table_name(production_mode, fake_db):
    """Schema-prefix decision (Phase 1.5 db.py docstring): tables live in
    `public`, no `overwatch_v2.` prefix anywhere."""
    at.issue_token(
        proposal_id="p", proposal_payload={}, issuer="ian", ttl_seconds=60,
    )
    inserts = [s for s, _ in fake_db.executed
               if s.lstrip().upper().startswith("INSERT")]
    assert any("INSERT INTO approval_tokens" in s for s in inserts)
    assert not any("overwatch_v2.approval_tokens" in s for s in inserts), (
        "schema prefix re-introduced — see db.py docstring"
    )


def test_record_issue_succeeds_with_text_proposal_id(production_mode, fake_db):
    """`tool:comment_on_pr` is a TEXT proposal_id, not a UUID. Migration
    013 relaxed the column type so this synthesized form is valid."""
    tok = at.issue_token(
        proposal_id="tool:comment_on_pr",
        proposal_payload={"tool_name": "comment_on_pr",
                          "params": {"repo": "x", "pr_number": 1, "body": "y"}},
        issuer="ian@vaultscaler.com", ttl_seconds=60,
    )
    assert tok.count(".") == 2
    assert "tool:comment_on_pr" in [r["proposal_id"] for r in fake_db.rows.values()]


# ---- _consume -------------------------------------------------------------

def test_consume_returns_true_first_time_then_false(production_mode, fake_db):
    """Single-use enforcement at the DB layer: the atomic UPDATE … WHERE
    used=false RETURNING is the race-winner mechanism."""
    payload = {"x": 1}
    tok = at.issue_token("p", payload, "ian", ttl_seconds=60)
    r1 = at.verify_token(tok, "p", payload)
    assert r1.valid is True
    r2 = at.verify_token(tok, "p", payload)
    assert r2.valid is False
    assert r2.reason == "already_used"


def test_consume_unknown_token_returns_false_gracefully(production_mode, fake_db):
    """A forged token that signs correctly but references a token_id we've
    never seen must not be accepted."""
    # Mint a token in the fake DB, then forget it
    payload = {"x": 1}
    at.issue_token("p", payload, "ian", ttl_seconds=60)
    fake_db.rows.clear()
    # Mint a fresh token (no row in DB now)
    tok2 = at.issue_token("p", payload, "ian", ttl_seconds=60)
    fake_db.rows.clear()  # second token also forgotten
    r = at.verify_token(tok2, "p", payload)
    assert r.valid is False
    assert r.reason == "already_used"  # _consume sees no matching row


# ---- Equivalence between MODE=local and MODE=production -------------------

def test_local_and_postgres_paths_observably_equivalent(monkeypatch, fake_db):
    """For the same sequence of operations, MODE=local (in-memory dict) and
    MODE=production (Postgres via fake_db) yield the same observable
    outcomes. Drift between the two paths would silently weaken the gate."""
    payload = {"action": "x"}

    def run_sequence() -> list[bool]:
        at._reset_for_tests()
        tok = at.issue_token("p", payload, "ian", ttl_seconds=60)
        first = at.verify_token(tok, "p", payload).valid
        second = at.verify_token(tok, "p", payload).valid  # reuse
        third = at.verify_token(tok, "p-mismatch", payload).valid  # bad proposal
        return [first, second, third]

    monkeypatch.setattr(at, "MODE", "local")
    local_outcomes = run_sequence()
    monkeypatch.setattr(at, "MODE", "production")
    prod_outcomes = run_sequence()
    assert local_outcomes == prod_outcomes == [True, False, False]
