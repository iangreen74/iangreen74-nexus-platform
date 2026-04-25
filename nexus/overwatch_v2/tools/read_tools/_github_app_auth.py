"""GitHub App installation token minting for Echo's read_github tool.

Mints short-lived installation tokens (GitHub max 60 min) from the
overwatch-v2-reasoner App credentials stored in Secrets Manager.

Tokens cached in-process with a 50-min TTL so we refresh proactively
before GitHub expires them. App credentials cached at module level —
fetched once per process (lesson L from PR #14: never re-fetch
secrets in the hot path).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import jwt

GITHUB_APP_SECRET_ID = "overwatch-v2/github-app"
TOKEN_TTL_SECONDS = 50 * 60      # 50 min; GitHub installation tokens expire at 60
JWT_TTL_SECONDS = 9 * 60         # 9 min; GitHub App JWT max is 10
GITHUB_API = "https://api.github.com"

log = logging.getLogger(__name__)


@dataclass
class _CachedToken:
    token: str
    expires_at: float


_token_cache: Optional[_CachedToken] = None
_app_creds_cache: Optional[dict] = None


def _load_app_credentials() -> dict:
    """Fetch App ID, installation ID, private key from Secrets Manager.

    Cached at module level — first call hits Secrets Manager, all
    subsequent calls return the cached dict.
    """
    global _app_creds_cache
    if _app_creds_cache is not None:
        return _app_creds_cache

    from nexus.aws_client import _client
    raw = _client("secretsmanager").get_secret_value(
        SecretId=GITHUB_APP_SECRET_ID
    )["SecretString"]
    creds = json.loads(raw)

    for required in ("app_id", "installation_id", "private_key"):
        if not creds.get(required):
            raise RuntimeError(
                f"github-app secret missing field: {required}"
            )

    _app_creds_cache = creds
    return creds


def _mint_app_jwt(creds: dict) -> str:
    """Sign a short-lived JWT with the App's private key (RS256)."""
    now = int(time.time())
    payload = {
        "iat": now - 60,                # backdate 60s for clock skew
        "exp": now + JWT_TTL_SECONDS,   # 9 min ceiling
        "iss": str(creds["app_id"]),
    }
    return jwt.encode(payload, creds["private_key"], algorithm="RS256")


def _mint_installation_token(creds: dict, app_jwt: str) -> tuple[str, float]:
    """Exchange an App JWT for an installation token.

    Returns (token, unix_expires_at).
    """
    installation_id = creds["installation_id"]
    url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {app_jwt}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    with httpx.Client(timeout=10.0) as client:
        response = client.post(url, headers=headers)

    if response.status_code != 201:
        raise RuntimeError(
            f"github installation token mint failed: "
            f"{response.status_code} {response.text[:200]}"
        )

    token = response.json()["token"]
    expires_at = time.time() + TOKEN_TTL_SECONDS
    return token, expires_at


def get_installation_token() -> str:
    """Return a valid installation token, minting on cache miss/expiry.

    Use as: ``Authorization: Bearer {get_installation_token()}``.
    """
    global _token_cache

    if _token_cache is not None and time.time() < _token_cache.expires_at:
        return _token_cache.token

    creds = _load_app_credentials()
    app_jwt = _mint_app_jwt(creds)
    token, expires_at = _mint_installation_token(creds, app_jwt)

    _token_cache = _CachedToken(token=token, expires_at=expires_at)
    log.info(
        "minted github installation token; expires in %ds",
        int(expires_at - time.time()),
    )
    return token


def _reset_caches_for_test() -> None:
    """Test hook — clears module-level caches between test cases."""
    global _token_cache, _app_creds_cache
    _token_cache = None
    _app_creds_cache = None
