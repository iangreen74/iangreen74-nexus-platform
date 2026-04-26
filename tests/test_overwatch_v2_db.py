"""Tests for nexus.overwatch_v2.db.get_conn — the shared Postgres
connection helper introduced by Phase 1.5.

psycopg2 is mocked end-to-end; the codebase has no Postgres test container
or docker-compose pattern, so we match the MagicMock idiom used by every
other V2 test that exercises an external system."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock, patch

import pytest

from nexus.overwatch_v2 import db


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    monkeypatch.delenv("OVERWATCH_V2_DATABASE_URL", raising=False)
    for k in ("PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DBNAME"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_get_conn_raises_when_no_config_present():
    with pytest.raises(db.DBNotConfiguredError, match="Neither OVERWATCH_V2_DATABASE_URL"):
        with db.get_conn():
            pass


def test_compose_from_pg_vars_when_url_unset(monkeypatch):
    """Phase 1.5.1: postgres-master is the single source of truth; ECS task
    defs unpack its JSON into PG_* env vars. db.py composes the URL so we
    don't need a parallel pre-formatted-URL secret that drifts on rotation."""
    monkeypatch.setenv("PG_HOST", "overwatch-postgres.cj0quk64skxf.us-east-1.rds.amazonaws.com")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_USER", "overwatch_admin")
    monkeypatch.setenv("PG_PASSWORD", "p@ss/word with spaces")  # exercises quote_plus
    monkeypatch.setenv("PG_DBNAME", "overwatch")
    fake = MagicMock()
    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.return_value = fake
    with patch.dict("sys.modules", {"psycopg2": fake_psycopg2}):
        with db.get_conn():
            pass
    expected = (
        "postgres://overwatch_admin:p%40ss%2Fword+with+spaces@"
        "overwatch-postgres.cj0quk64skxf.us-east-1.rds.amazonaws.com:5432/overwatch"
    )
    fake_psycopg2.connect.assert_called_once_with(expected, connect_timeout=5)


def test_partial_pg_vars_does_not_compose(monkeypatch):
    """Missing one of the five components must NOT silently compose a malformed
    URL — it must raise so the operator notices the gap."""
    monkeypatch.setenv("PG_HOST", "h")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_USER", "u")
    # PG_PASSWORD intentionally unset
    monkeypatch.setenv("PG_DBNAME", "n")
    with pytest.raises(db.DBNotConfiguredError, match="full set"):
        with db.get_conn():
            pass


def test_url_takes_priority_over_pg_vars(monkeypatch):
    """If both are set, OVERWATCH_V2_DATABASE_URL wins — single-string
    config remains the explicit override path."""
    monkeypatch.setenv("OVERWATCH_V2_DATABASE_URL", "postgres://from-url")
    monkeypatch.setenv("PG_HOST", "from-pg-vars")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_USER", "u")
    monkeypatch.setenv("PG_PASSWORD", "p")
    monkeypatch.setenv("PG_DBNAME", "n")
    fake = MagicMock()
    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.return_value = fake
    with patch.dict("sys.modules", {"psycopg2": fake_psycopg2}):
        with db.get_conn():
            pass
    fake_psycopg2.connect.assert_called_once_with("postgres://from-url", connect_timeout=5)


def test_get_conn_passes_url_and_timeout_to_psycopg2(monkeypatch):
    monkeypatch.setenv("OVERWATCH_V2_DATABASE_URL", "postgres://u:p@h:5432/n")
    fake = MagicMock()
    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.return_value = fake
    with patch.dict("sys.modules", {"psycopg2": fake_psycopg2}):
        with db.get_conn() as conn:
            assert conn is fake
    fake_psycopg2.connect.assert_called_once_with(
        "postgres://u:p@h:5432/n", connect_timeout=5
    )


def test_get_conn_commits_on_success(monkeypatch):
    monkeypatch.setenv("OVERWATCH_V2_DATABASE_URL", "x")
    fake = MagicMock()
    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.return_value = fake
    with patch.dict("sys.modules", {"psycopg2": fake_psycopg2}):
        with db.get_conn():
            pass
    fake.commit.assert_called_once()
    fake.rollback.assert_not_called()
    fake.close.assert_called_once()


def test_get_conn_rolls_back_on_exception(monkeypatch):
    monkeypatch.setenv("OVERWATCH_V2_DATABASE_URL", "x")
    fake = MagicMock()
    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.return_value = fake
    with patch.dict("sys.modules", {"psycopg2": fake_psycopg2}):
        with pytest.raises(RuntimeError, match="boom"):
            with db.get_conn():
                raise RuntimeError("boom")
    fake.rollback.assert_called_once()
    fake.commit.assert_not_called()
    fake.close.assert_called_once()
