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
    yield


def test_get_conn_raises_when_url_unset():
    with pytest.raises(db.DBNotConfiguredError, match="OVERWATCH_V2_DATABASE_URL"):
        with db.get_conn():
            pass


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
