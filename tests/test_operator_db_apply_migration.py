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
