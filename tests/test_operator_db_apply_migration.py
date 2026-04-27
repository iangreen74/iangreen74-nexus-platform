"""Tests for nexus.operator.db_apply_migration — operator-runbook script
that bridges 'manual ad-hoc psql' to a recordable apply by stamping a
schema_migrations row inside the same transaction as the migration SQL."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from nexus.operator import db_apply_migration as runner


@pytest.fixture
def tmp_migration(tmp_path):
    p = tmp_path / "099_test_migration.sql"
    p.write_text("CREATE TABLE foo (id INT);", encoding="utf-8")
    return p


@pytest.fixture
def fake_get_conn(monkeypatch):
    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.fetchone.return_value = None  # not previously applied by default
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _fake():
        yield conn

    monkeypatch.setattr(runner, "get_conn", _fake)
    return cur


def test_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        runner.apply_migration(tmp_path / "does_not_exist.sql")


def test_apply_creates_ledger_then_runs_sql_then_records(
    fake_get_conn, tmp_migration, capsys
):
    runner.apply_migration(tmp_migration)
    sqls = [c.args[0] for c in fake_get_conn.execute.call_args_list]
    # 1: CREATE TABLE IF NOT EXISTS schema_migrations
    # 2: SELECT checksum, applied_at FROM schema_migrations WHERE filename=...
    # 3: the migration body
    # 4: INSERT INTO schema_migrations
    assert "schema_migrations" in sqls[0]
    assert "SELECT checksum" in sqls[1]
    assert "CREATE TABLE foo" in sqls[2]
    assert sqls[3].startswith("INSERT INTO schema_migrations")
    out = capsys.readouterr().out
    assert "applied: 099_test_migration.sql" in out


def test_already_applied_with_matching_checksum_is_idempotent_noop(
    fake_get_conn, tmp_migration, capsys
):
    """Phase 1.5.1: idempotent re-runs by the one-shot task wrapper must
    succeed when the recorded checksum matches the file on disk — otherwise
    the wrapper can't safely re-attempt verification without the operator
    re-applying."""
    import hashlib
    from datetime import datetime, timezone
    matching = hashlib.sha256(tmp_migration.read_bytes()).hexdigest()
    fake_get_conn.fetchone.return_value = (
        matching, datetime(2026, 4, 26, 20, 30, 0, tzinfo=timezone.utc),
    )
    runner.apply_migration(tmp_migration)  # MUST NOT raise
    sqls = [c.args[0] for c in fake_get_conn.execute.call_args_list]
    assert not any("CREATE TABLE foo" in s for s in sqls), \
        "must not re-execute migration body"
    assert not any(s.startswith("INSERT INTO schema_migrations") for s in sqls), \
        "must not re-record"
    out = capsys.readouterr().out
    assert "already applied (matching)" in out


def test_refuses_to_reapply_on_checksum_drift(fake_get_conn, tmp_migration):
    """If the recorded checksum differs from the file on disk, surface
    drift instead of silently skipping or re-applying."""
    from datetime import datetime, timezone
    fake_get_conn.fetchone.return_value = (
        "DIFFERENT-checksum-than-file",
        datetime(2026, 4, 26, tzinfo=timezone.utc),
    )
    with pytest.raises(SystemExit, match="drift detected"):
        runner.apply_migration(tmp_migration)
    sqls = [c.args[0] for c in fake_get_conn.execute.call_args_list]
    assert not any("CREATE TABLE foo" in s for s in sqls)
    assert not any(s.startswith("INSERT INTO schema_migrations") for s in sqls)


def test_records_sha256_checksum(fake_get_conn, tmp_migration):
    import hashlib
    expected = hashlib.sha256(tmp_migration.read_bytes()).hexdigest()
    runner.apply_migration(tmp_migration)
    insert_call = next(
        c for c in fake_get_conn.execute.call_args_list
        if c.args[0].startswith("INSERT INTO schema_migrations")
    )
    _, params = insert_call.args
    assert params == ("099_test_migration.sql", expected)


def test_main_requires_one_argument():
    with pytest.raises(SystemExit, match="usage"):
        runner.main([])
    with pytest.raises(SystemExit, match="usage"):
        runner.main(["a", "b"])


# ---------------------------------------------------------------------------
# Target routing — V1/V2 split (Sprint 15 Day 3, Bug 4 substrate fix)
# ---------------------------------------------------------------------------

def test_parse_args_default_target_is_v2():
    """Backward compat: existing V2 callers don't need to pass --target."""
    path, target = runner._parse_args(["migrations/099.sql"])
    assert path == "migrations/099.sql"
    assert target == "v2"


def test_parse_args_target_v1_explicit():
    path, target = runner._parse_args(["migrations/099.sql", "--target=v1"])
    assert path == "migrations/099.sql"
    assert target == "v1"


def test_parse_args_unknown_target_errors():
    with pytest.raises(SystemExit, match="unknown migration target"):
        runner._parse_args(["migrations/099.sql", "--target=v3"])


def test_parse_args_target_space_separated_rejected():
    """Reject `--target v1` to avoid the silent-wrong-target bug class —
    space-separated could conflict with a positional arg if file paths
    ever contained 'v1'."""
    with pytest.raises(SystemExit, match="use '='"):
        runner._parse_args(["migrations/099.sql", "--target", "v1"])


def test_open_target_conn_v2_routes_through_module_get_conn(monkeypatch):
    """V2 path uses the module-level `get_conn` symbol so existing
    monkeypatches on `runner.get_conn` keep working unchanged."""
    sentinel = object()

    @contextmanager
    def _fake_v2():
        yield sentinel

    monkeypatch.setattr(runner, "get_conn", _fake_v2)
    cm = runner._open_target_conn("v2")
    with cm as conn:
        assert conn is sentinel


def test_open_target_conn_v1_routes_through_database_url(monkeypatch):
    """V1 path reads DATABASE_URL from env and connects via psycopg2."""
    monkeypatch.setenv("DATABASE_URL", "postgres://fake:fake@host/db")
    fake_conn = MagicMock()
    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.return_value = fake_conn

    import sys as _sys
    monkeypatch.setitem(_sys.modules, "psycopg2", fake_psycopg2)

    cm = runner._open_target_conn("v1")
    with cm as conn:
        assert conn is fake_conn
    fake_psycopg2.connect.assert_called_once_with(
        "postgres://fake:fake@host/db", connect_timeout=5
    )
    fake_conn.commit.assert_called_once()
    fake_conn.close.assert_called_once()


def test_open_target_conn_v1_missing_url_raises(monkeypatch):
    """V1 path errors loudly when DATABASE_URL is unset."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cm = runner._open_target_conn("v1")
    with pytest.raises(runner.V1DBNotConfiguredError, match="DATABASE_URL not set"):
        with cm:
            pass


def test_open_target_conn_unknown_target_raises():
    with pytest.raises(ValueError, match="unknown migration target"):
        runner._open_target_conn("v3")


def test_apply_idempotent_passes_target_to_ledger_check(monkeypatch, tmp_path):
    """V1 apply uses the V1 connection — verify target='v1' wires through
    by intercepting `_open_target_conn` and asserting the target arg."""
    p = tmp_path / "099_v1.sql"
    p.write_text("CREATE TABLE bar (id INT);", encoding="utf-8")

    cur = MagicMock()
    cur.__enter__ = lambda self: cur
    cur.__exit__ = lambda self, *a: False
    cur.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cur

    captured: dict[str, str] = {}

    @contextmanager
    def _fake_open(target: str):
        captured["target"] = target
        yield conn

    monkeypatch.setattr(runner, "_open_target_conn", _fake_open)
    result = runner.apply_migration_idempotent(p, target="v1")
    assert captured["target"] == "v1"
    assert result["target"] == "v1"
    assert result["status"] == "applied"
