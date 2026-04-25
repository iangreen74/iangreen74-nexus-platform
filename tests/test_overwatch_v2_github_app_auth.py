"""Tests for nexus.overwatch_v2.tools.read_tools._github_app_auth.

Covers JWT minting, installation-token mint, in-process token cache,
App-credentials cache, and the failure paths (network errors, missing
secret fields, 401 from GitHub).
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest

from nexus.overwatch_v2.tools.read_tools import _github_app_auth as auth_mod


def _generate_test_private_key() -> str:
    """Generate a throwaway 2048-bit RSA key for these tests only.

    Runtime-generated so we don't ship a static embedded key (which
    triggers secret-scanning false positives) and so the bytes are
    guaranteed to be a parseable RSA key.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


_TEST_PRIVATE_KEY = _generate_test_private_key()
_TEST_CREDS = {
    "app_id": "9999999",
    "installation_id": "888888",
    "private_key": _TEST_PRIVATE_KEY,
}


@pytest.fixture(autouse=True)
def _reset_caches():
    auth_mod._reset_caches_for_test()
    yield
    auth_mod._reset_caches_for_test()


# --- _mint_app_jwt ---------------------------------------------------------

def test_mint_app_jwt_signs_with_rs256():
    token = auth_mod._mint_app_jwt(_TEST_CREDS)
    headers = jwt.get_unverified_header(token)
    assert headers["alg"] == "RS256"


def test_mint_app_jwt_carries_app_id_and_short_expiry():
    now = int(time.time())
    token = auth_mod._mint_app_jwt(_TEST_CREDS)
    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == _TEST_CREDS["app_id"]
    assert decoded["iat"] <= now
    # Expiry under 10 minutes (GitHub's hard ceiling)
    assert decoded["exp"] - now <= 10 * 60
    # And not in the past
    assert decoded["exp"] > now


def test_mint_app_jwt_backdates_iat_for_clock_skew():
    now = int(time.time())
    token = auth_mod._mint_app_jwt(_TEST_CREDS)
    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["iat"] < now  # at least a few seconds backdated


# --- _load_app_credentials -------------------------------------------------

def test_load_app_credentials_caches_after_first_call():
    fake_secret = json.dumps(_TEST_CREDS)
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {"SecretString": fake_secret}
    with patch("nexus.aws_client._client", return_value=fake_client):
        creds1 = auth_mod._load_app_credentials()
        creds2 = auth_mod._load_app_credentials()
    assert creds1 is creds2
    fake_client.get_secret_value.assert_called_once()


def test_load_app_credentials_missing_field_raises():
    bad = json.dumps({"app_id": "x", "installation_id": "y"})  # no private_key
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {"SecretString": bad}
    with patch("nexus.aws_client._client", return_value=fake_client):
        with pytest.raises(RuntimeError, match="private_key"):
            auth_mod._load_app_credentials()


def test_load_app_credentials_empty_field_raises():
    bad = json.dumps({"app_id": "x", "installation_id": "y", "private_key": ""})
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {"SecretString": bad}
    with patch("nexus.aws_client._client", return_value=fake_client):
        with pytest.raises(RuntimeError, match="private_key"):
            auth_mod._load_app_credentials()


# --- _mint_installation_token ----------------------------------------------

def test_mint_installation_token_happy():
    fake_resp = MagicMock(status_code=201)
    fake_resp.json.return_value = {"token": "ghs_test_xyz"}
    with patch("httpx.Client") as cls:
        cls.return_value.__enter__.return_value.post.return_value = fake_resp
        token, exp = auth_mod._mint_installation_token(_TEST_CREDS, "jwt-x")
    assert token == "ghs_test_xyz"
    assert exp > time.time() + 30 * 60  # at least 30 min in the future


def test_mint_installation_token_401_raises():
    fake_resp = MagicMock(status_code=401)
    fake_resp.text = '{"message":"Bad credentials"}'
    with patch("httpx.Client") as cls:
        cls.return_value.__enter__.return_value.post.return_value = fake_resp
        with pytest.raises(RuntimeError, match="401"):
            auth_mod._mint_installation_token(_TEST_CREDS, "jwt-x")


def test_mint_installation_token_500_raises():
    fake_resp = MagicMock(status_code=500)
    fake_resp.text = "boom"
    with patch("httpx.Client") as cls:
        cls.return_value.__enter__.return_value.post.return_value = fake_resp
        with pytest.raises(RuntimeError, match="500"):
            auth_mod._mint_installation_token(_TEST_CREDS, "jwt-x")


def test_mint_installation_token_network_error_propagates():
    import httpx
    with patch("httpx.Client") as cls:
        cls.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError("boom")
        with pytest.raises(httpx.ConnectError):
            auth_mod._mint_installation_token(_TEST_CREDS, "jwt-x")


# --- get_installation_token (full path with caching) -----------------------

def test_get_installation_token_caches_within_ttl():
    fake_secret = json.dumps(_TEST_CREDS)
    sm_client = MagicMock()
    sm_client.get_secret_value.return_value = {"SecretString": fake_secret}
    fake_resp = MagicMock(status_code=201)
    fake_resp.json.return_value = {"token": "ghs_first"}
    with patch("nexus.aws_client._client", return_value=sm_client), \
         patch("httpx.Client") as cls:
        cls.return_value.__enter__.return_value.post.return_value = fake_resp
        t1 = auth_mod.get_installation_token()
        t2 = auth_mod.get_installation_token()
    assert t1 == t2 == "ghs_first"
    # Secrets Manager fetched once; httpx mint called once (cache hit on 2nd)
    sm_client.get_secret_value.assert_called_once()
    cls.return_value.__enter__.return_value.post.assert_called_once()


def test_get_installation_token_refreshes_after_ttl_expiry():
    fake_secret = json.dumps(_TEST_CREDS)
    sm_client = MagicMock()
    sm_client.get_secret_value.return_value = {"SecretString": fake_secret}
    resp1 = MagicMock(status_code=201); resp1.json.return_value = {"token": "tok-1"}
    resp2 = MagicMock(status_code=201); resp2.json.return_value = {"token": "tok-2"}
    with patch("nexus.aws_client._client", return_value=sm_client), \
         patch("httpx.Client") as cls:
        cls.return_value.__enter__.return_value.post.side_effect = [resp1, resp2]
        t1 = auth_mod.get_installation_token()
        # Force expiry on the cached token
        auth_mod._token_cache.expires_at = time.time() - 1
        t2 = auth_mod.get_installation_token()
    assert t1 == "tok-1"
    assert t2 == "tok-2"
    # Creds cache held; no second secrets fetch
    sm_client.get_secret_value.assert_called_once()


def test_get_installation_token_surfaces_mint_failure():
    fake_secret = json.dumps(_TEST_CREDS)
    sm_client = MagicMock()
    sm_client.get_secret_value.return_value = {"SecretString": fake_secret}
    fake_resp = MagicMock(status_code=403)
    fake_resp.text = "forbidden"
    with patch("nexus.aws_client._client", return_value=sm_client), \
         patch("httpx.Client") as cls:
        cls.return_value.__enter__.return_value.post.return_value = fake_resp
        with pytest.raises(RuntimeError, match="403"):
            auth_mod.get_installation_token()


def test_get_installation_token_surfaces_secret_missing_field():
    bad = json.dumps({"app_id": "x"})
    sm_client = MagicMock()
    sm_client.get_secret_value.return_value = {"SecretString": bad}
    with patch("nexus.aws_client._client", return_value=sm_client):
        with pytest.raises(RuntimeError, match="installation_id"):
            auth_mod.get_installation_token()


# --- read_github wiring ----------------------------------------------------

def test_read_github_headers_uses_installation_token():
    from nexus.overwatch_v2.tools.read_tools import github
    with patch.object(github, "get_installation_token", return_value="ghs-fake"):
        headers = github._headers()
    assert headers["Authorization"] == "Bearer ghs-fake"
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_read_github_headers_wraps_auth_failure_as_tool_unknown():
    from nexus.overwatch_v2.tools.read_tools import github
    from nexus.overwatch_v2.tools.read_tools.exceptions import ToolUnknown
    with patch.object(github, "get_installation_token",
                      side_effect=RuntimeError("secret unavailable")):
        with pytest.raises(ToolUnknown, match="github auth failed"):
            github._headers()
