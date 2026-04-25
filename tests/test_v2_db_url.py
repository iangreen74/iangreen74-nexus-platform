"""Tests for V2 Postgres URL resolution.

Two-tier resolution: env var first, then Secrets Manager fallback. Local
mode (no env, mocked Secrets Manager) returns None; the persistence layer
degrades gracefully to the in-memory store.
"""
import os
os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from nexus.aria_v2 import db  # noqa: E402


def setup_function():
    db.reset_cache_for_tests()


def test_env_var_wins_when_set(monkeypatch):
    monkeypatch.setenv("OVERWATCH_V2_DATABASE_URL", "postgresql://from-env/db")
    assert db.database_url() == "postgresql://from-env/db"


def test_env_var_blank_treated_as_unset(monkeypatch):
    monkeypatch.setenv("OVERWATCH_V2_DATABASE_URL", "   ")
    # Empty/whitespace env should fall through to secrets manager (which fails
    # because boto3 will be mocked to raise — see next test for that flow).
    fake_sm = MagicMock()
    fake_sm.get_secret_value.side_effect = RuntimeError("no creds")
    fake_boto = MagicMock()
    fake_boto.client.return_value = fake_sm
    with patch.dict("sys.modules", {"boto3": fake_boto}):
        assert db.database_url() is None


def test_env_var_unset_falls_back_to_secrets_manager(monkeypatch):
    monkeypatch.delenv("OVERWATCH_V2_DATABASE_URL", raising=False)
    fake_sm = MagicMock()
    fake_sm.get_secret_value.return_value = {
        "SecretString": '{"username":"u","password":"p"}'
    }
    fake_boto = MagicMock()
    fake_boto.client.return_value = fake_sm
    with patch.dict("sys.modules", {"boto3": fake_boto}):
        url = db.database_url()
    assert url is not None
    assert url.startswith("postgresql://u:p@")
    assert ":5432/overwatch" in url
    assert "overwatch-postgres" in url


def test_secrets_manager_failure_returns_none(monkeypatch):
    monkeypatch.delenv("OVERWATCH_V2_DATABASE_URL", raising=False)
    fake_boto = MagicMock()
    fake_boto.client.side_effect = RuntimeError("boom")
    with patch.dict("sys.modules", {"boto3": fake_boto}):
        assert db.database_url() is None


def test_secrets_fallback_is_cached(monkeypatch):
    monkeypatch.delenv("OVERWATCH_V2_DATABASE_URL", raising=False)
    fake_sm = MagicMock()
    fake_sm.get_secret_value.return_value = {
        "SecretString": '{"username":"u","password":"p"}'
    }
    fake_boto = MagicMock()
    fake_boto.client.return_value = fake_sm
    with patch.dict("sys.modules", {"boto3": fake_boto}):
        db.database_url()
        db.database_url()
        db.database_url()
    # boto3.client should be invoked exactly once thanks to lru_cache.
    assert fake_boto.client.call_count == 1


def test_reset_cache_clears_lru(monkeypatch):
    monkeypatch.delenv("OVERWATCH_V2_DATABASE_URL", raising=False)
    fake_sm = MagicMock()
    fake_sm.get_secret_value.return_value = {
        "SecretString": '{"username":"u","password":"p"}'
    }
    fake_boto = MagicMock()
    fake_boto.client.return_value = fake_sm
    with patch.dict("sys.modules", {"boto3": fake_boto}):
        db.database_url()
        assert fake_boto.client.call_count == 1
        db.reset_cache_for_tests()
        db.database_url()
        assert fake_boto.client.call_count == 2
