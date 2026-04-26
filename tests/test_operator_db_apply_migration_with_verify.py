"""Tests for the one-shot apply+verify wrapper that runs as the
aria-console-migration-apply ECS task."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from nexus.operator import db_apply_migration_with_verify as wrapper
from nexus.operator.db_apply_migration import (
    STATUS_APPLIED, STATUS_ALREADY_APPLIED_MATCHING,
)


@pytest.fixture
def tmp_migration(tmp_path):
    p = tmp_path / "013_test.sql"
    p.write_text("CREATE TABLE foo (id INT);", encoding="utf-8")
    return p


@pytest.fixture
def stub_apply_ok(monkeypatch):
    """Default: apply returns 'applied' (fresh)."""
    monkeypatch.setattr(wrapper, "apply_migration_idempotent",
                        lambda path: {
                            "status": STATUS_APPLIED,
                            "filename": os.path.basename(path),
                            "checksum_sha256": "a" * 64,
                            "applied_at_utc": "2026-04-26T20:30:00+00:00",
                        })


def _stub_verifier(monkeypatch, name, result):
    monkeypatch.setattr(wrapper, name, lambda *a, **kw: result)


def _stub_all_verifiers_ok(monkeypatch):
    _stub_verifier(monkeypatch, "_verify_schema_migrations_row",
                   {"ok": True, "details": {"filename": "013_test.sql",
                                             "applied_at_utc": "2026-04-26T20:30:00+00:00",
                                             "checksum_sha12": "abcdef012345"}})
    _stub_verifier(monkeypatch, "_verify_approval_tokens_columns",
                   {"ok": True, "details": {"actual": {
                       "issuer": "text", "proposal_hash": "text",
                       "proposal_id": "text"}}})
    _stub_verifier(monkeypatch, "_verify_fk_gone",
                   {"ok": True, "details": {"foreign_keys_remaining": []}})
    _stub_verifier(monkeypatch, "_smoke_test",
                   {"ok": True, "details": {
                       "sentinel_proposal_id": "tool:phase15-smoke-deadbeef",
                       "valid": True}})


# ---- Happy path -----------------------------------------------------------

def test_run_all_steps_pass(stub_apply_ok, monkeypatch, tmp_migration):
    _stub_all_verifiers_ok(monkeypatch)
    result = wrapper.run(str(tmp_migration))
    assert result["ok"] is True
    assert result["failed_step"] is None
    assert result["apply_status"] == STATUS_APPLIED
    for name in ("apply", "verify_schema_migrations", "verify_columns",
                 "verify_fk_gone", "smoke_test"):
        assert result["steps"][name]["ok"] is True


def test_main_returns_zero_on_full_success(
    stub_apply_ok, monkeypatch, tmp_migration, capsys
):
    _stub_all_verifiers_ok(monkeypatch)
    rc = wrapper.main([str(tmp_migration)])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["ok"] is True


def test_idempotent_apply_still_runs_verifications(monkeypatch, tmp_migration):
    """Re-running the task on a previously-applied migration must verify the
    schema state, not skip everything."""
    monkeypatch.setattr(wrapper, "apply_migration_idempotent",
                        lambda path: {
                            "status": STATUS_ALREADY_APPLIED_MATCHING,
                            "filename": os.path.basename(path),
                            "checksum_sha256": "b" * 64,
                            "applied_at_utc": "2026-04-26T20:30:00+00:00",
                        })
    _stub_all_verifiers_ok(monkeypatch)
    result = wrapper.run(str(tmp_migration))
    assert result["ok"] is True
    assert result["apply_status"] == STATUS_ALREADY_APPLIED_MATCHING
    assert result["steps"]["smoke_test"]["ok"] is True


# ---- Failure modes — all-or-nothing --------------------------------------

def test_apply_failure_short_circuits(monkeypatch, tmp_migration):
    def boom(_):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(wrapper, "apply_migration_idempotent", boom)
    result = wrapper.run(str(tmp_migration))
    assert result["ok"] is False
    assert result["failed_step"] == "apply"
    assert "connection refused" in result["steps"]["apply"]["details"]["error"]
    # Verifiers must not have been touched — they appear with ok=None
    for name in ("verify_schema_migrations", "verify_columns",
                 "verify_fk_gone", "smoke_test"):
        assert result["steps"][name]["ok"] is None


def test_verify_columns_failure_halts_pipeline(
    stub_apply_ok, monkeypatch, tmp_migration
):
    _stub_verifier(monkeypatch, "_verify_schema_migrations_row",
                   {"ok": True, "details": {}})
    _stub_verifier(monkeypatch, "_verify_approval_tokens_columns",
                   {"ok": False, "details": {"missing": ["issuer"]}})
    # smoke_test should never be called
    boom = MagicMock(side_effect=AssertionError("smoke_test should not run"))
    monkeypatch.setattr(wrapper, "_smoke_test", boom)
    monkeypatch.setattr(wrapper, "_verify_fk_gone", boom)
    result = wrapper.run(str(tmp_migration))
    assert result["ok"] is False
    assert result["failed_step"] == "verify_columns"
    boom.assert_not_called()


def test_smoke_test_failure_marks_overall_failure(
    stub_apply_ok, monkeypatch, tmp_migration
):
    _stub_verifier(monkeypatch, "_verify_schema_migrations_row",
                   {"ok": True, "details": {}})
    _stub_verifier(monkeypatch, "_verify_approval_tokens_columns",
                   {"ok": True, "details": {}})
    _stub_verifier(monkeypatch, "_verify_fk_gone",
                   {"ok": True, "details": {}})
    _stub_verifier(monkeypatch, "_smoke_test",
                   {"ok": False, "details": {"error": "verify_token invalid: bad_signature"}})
    result = wrapper.run(str(tmp_migration))
    assert result["ok"] is False
    assert result["failed_step"] == "smoke_test"


def test_main_returns_one_on_any_failure(
    stub_apply_ok, monkeypatch, tmp_migration, capsys
):
    _stub_verifier(monkeypatch, "_verify_schema_migrations_row",
                   {"ok": False, "details": {"error": "no row"}})
    rc = wrapper.main([str(tmp_migration)])
    assert rc == 1
    parsed = json.loads(capsys.readouterr().out.strip())
    assert parsed["ok"] is False
    assert parsed["failed_step"] == "verify_schema_migrations"


def test_main_exits_two_on_bad_args(capsys):
    rc = wrapper.main([])
    assert rc == 2
    parsed = json.loads(capsys.readouterr().out.strip())
    assert parsed["ok"] is False
    assert parsed["failed_step"] == "args"


# ---- Verification helpers in isolation -----------------------------------

def test_verify_schema_migrations_row_missing(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.fetchone.return_value = None
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _fake(): yield conn
    monkeypatch.setattr(wrapper, "get_conn", _fake)
    r = wrapper._verify_schema_migrations_row("013_test.sql")
    assert r["ok"] is False
    assert "no row" in r["details"]["error"]


def test_verify_columns_detects_missing(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.fetchall.return_value = [("issuer", "text"), ("proposal_id", "text")]  # missing proposal_hash
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _fake(): yield conn
    monkeypatch.setattr(wrapper, "get_conn", _fake)
    r = wrapper._verify_approval_tokens_columns()
    assert r["ok"] is False
    assert "proposal_hash" in r["details"]["missing"]


def test_verify_columns_detects_wrong_type(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.fetchall.return_value = [
        ("issuer", "text"), ("proposal_hash", "text"), ("proposal_id", "uuid"),
    ]
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _fake(): yield conn
    monkeypatch.setattr(wrapper, "get_conn", _fake)
    r = wrapper._verify_approval_tokens_columns()
    assert r["ok"] is False
    assert any("proposal_id" in s for s in r["details"]["wrong_type"])


def test_verify_fk_gone_flags_remaining_proposal_id_fk(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.fetchall.return_value = [("approval_tokens_proposal_id_fkey", "f")]
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _fake(): yield conn
    monkeypatch.setattr(wrapper, "get_conn", _fake)
    r = wrapper._verify_fk_gone()
    assert r["ok"] is False
    assert "approval_tokens_proposal_id_fkey" in r["details"]["constraints"]


def test_verify_fk_gone_passes_when_no_proposal_id_fk(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.fetchall.return_value = []
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _fake(): yield conn
    monkeypatch.setattr(wrapper, "get_conn", _fake)
    r = wrapper._verify_fk_gone()
    assert r["ok"] is True
