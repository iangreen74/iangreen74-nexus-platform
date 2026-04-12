"""Tests for Forgewing API auth injection."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock, patch  # noqa: E402

from nexus.capabilities import forgewing_api  # noqa: E402


def _clear_key_cache():
    forgewing_api._api_key_cache = None


# --- Key cache behavior ------------------------------------------------------


def test_local_mode_returns_empty_key():
    _clear_key_cache()
    assert forgewing_api._get_api_key() == ""


def test_key_cache_reuses_value():
    _clear_key_cache()
    forgewing_api._api_key_cache = "cached-key"
    assert forgewing_api._get_api_key() == "cached-key"


def test_invalidate_clears_cache():
    forgewing_api._api_key_cache = "stale"
    forgewing_api._invalidate_key_cache()
    assert forgewing_api._api_key_cache is None


# --- Production key fetching -------------------------------------------------


def test_key_fetch_parses_json_secret():
    _clear_key_cache()
    with patch("nexus.capabilities.forgewing_api.MODE", "production"):
        fake_sm = MagicMock()
        fake_sm.get_secret_value.return_value = {
            "SecretString": '{"api_key": "sk-test-abc"}'
        }
        fake_boto = MagicMock()
        fake_boto.client.return_value = fake_sm
        with patch.dict("sys.modules", {"boto3": fake_boto}):
            key = forgewing_api._get_api_key()
    assert key == "sk-test-abc"
    _clear_key_cache()


def test_key_fetch_handles_plain_string():
    _clear_key_cache()
    with patch("nexus.capabilities.forgewing_api.MODE", "production"):
        fake_sm = MagicMock()
        fake_sm.get_secret_value.return_value = {"SecretString": "plain-key-xyz"}
        fake_boto = MagicMock()
        fake_boto.client.return_value = fake_sm
        with patch.dict("sys.modules", {"boto3": fake_boto}):
            key = forgewing_api._get_api_key()
    assert key == "plain-key-xyz"
    _clear_key_cache()


def test_key_fetch_handles_errors_gracefully():
    _clear_key_cache()
    with patch("nexus.capabilities.forgewing_api.MODE", "production"):
        fake_sm = MagicMock()
        fake_sm.get_secret_value.side_effect = Exception("AccessDenied")
        fake_boto = MagicMock()
        fake_boto.client.return_value = fake_sm
        with patch.dict("sys.modules", {"boto3": fake_boto}):
            key = forgewing_api._get_api_key()
    assert key == ""  # returns empty, doesn't raise
    _clear_key_cache()


# --- call_api with auth injection --------------------------------------------


def test_call_api_attaches_header_in_production():
    """In production, X-API-Key header is attached from the cache."""
    _clear_key_cache()
    forgewing_api._api_key_cache = "test-key-123"
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True}
        return resp

    with patch("nexus.capabilities.forgewing_api.MODE", "production"), \
         patch("nexus.capabilities.forgewing_api.httpx.request", side_effect=fake_request):
        result = forgewing_api.call_api("GET", "/health")

    assert captured["headers"].get("X-API-Key") == "test-key-123"
    assert result == {"ok": True}
    _clear_key_cache()


def test_call_api_retries_on_401_with_fresh_key():
    """401 response triggers cache invalidation + retry."""
    _clear_key_cache()
    forgewing_api._api_key_cache = "stale-key"
    call_count = {"n": 0}

    def fake_request(method, url, **kwargs):
        call_count["n"] += 1
        resp = MagicMock()
        if call_count["n"] == 1:
            resp.status_code = 401
            resp.text = "Invalid API key"
        else:
            resp.status_code = 200
            resp.json.return_value = {"retried": True}
        return resp

    # After invalidation, _get_api_key would return "" in local-mode patch,
    # so the retry branch only fires if the new key differs from the old
    def fake_get_key():
        if forgewing_api._api_key_cache is None:
            forgewing_api._api_key_cache = "fresh-key"
        return forgewing_api._api_key_cache

    with patch("nexus.capabilities.forgewing_api.MODE", "production"), \
         patch("nexus.capabilities.forgewing_api.httpx.request", side_effect=fake_request), \
         patch("nexus.capabilities.forgewing_api._get_api_key", side_effect=fake_get_key):
        result = forgewing_api.call_api("GET", "/projects/x")

    assert call_count["n"] == 2  # initial + retry
    assert result == {"retried": True}
    _clear_key_cache()


def test_call_api_no_key_still_sends_request():
    """If no key available, request still fires (no header)."""
    _clear_key_cache()
    forgewing_api._api_key_cache = ""
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True}
        return resp

    with patch("nexus.capabilities.forgewing_api.MODE", "production"), \
         patch("nexus.capabilities.forgewing_api.httpx.request", side_effect=fake_request):
        forgewing_api.call_api("GET", "/health")

    assert "X-API-Key" not in captured["headers"]
    _clear_key_cache()


def test_call_api_local_mode_mock():
    """Local mode returns mock without hitting network or Secrets Manager."""
    result = forgewing_api.call_api("GET", "/health")
    assert result.get("mock") is True
