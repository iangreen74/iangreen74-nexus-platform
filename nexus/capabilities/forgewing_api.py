"""
Forgewing API Client — Overwatch's interface to the Forgewing platform.

All interactions go through the public API endpoints. Overwatch never
imports from aria-platform. This client attaches the X-API-Key header
from Secrets Manager (`forgescaler/api` → `api_key`) automatically.

In local mode, every call returns a mock result so the capability layer
can be tested without network access.
"""
from __future__ import annotations

import json as _json
import logging
from typing import Any

import httpx

from nexus.config import AWS_REGION, FORGEWING_API, HEALTH_CHECK_TIMEOUT_SECONDS, MODE

logger = logging.getLogger("nexus.capabilities.forgewing_api")

# Process-local cache for the API key — Secrets Manager is slow and
# every Overwatch poll would fetch otherwise. Refreshed on 401/403.
_api_key_cache: str | None = None


def _get_api_key() -> str:
    """Fetch the Forgewing X-API-Key from Secrets Manager, cached."""
    global _api_key_cache
    if _api_key_cache is not None:
        return _api_key_cache
    if MODE != "production":
        _api_key_cache = ""
        return ""
    try:
        import boto3  # noqa: WPS433 — lazy

        sm = boto3.client("secretsmanager", region_name=AWS_REGION)
        raw = sm.get_secret_value(SecretId="forgescaler/api")["SecretString"]
        if raw.startswith("{"):
            _api_key_cache = _json.loads(raw).get("api_key", "")
        else:
            _api_key_cache = raw.strip()
    except Exception as exc:
        logger.warning("Could not fetch forgescaler/api key: %s", exc)
        _api_key_cache = ""
    return _api_key_cache


def _invalidate_key_cache() -> None:
    """Clear the cached API key (e.g. after a 401)."""
    global _api_key_cache
    _api_key_cache = None


def call_api(
    method: str,
    path: str,
    data: dict[str, Any] | None = None,
    *,
    timeout: int = HEALTH_CHECK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """
    Authenticated request to the Forgewing API.

    Returns the JSON body on success, or {"error": ..., "status": N}
    on failure. Never raises — callers can always branch on error key.
    """
    url = f"{FORGEWING_API}{path}"
    if MODE != "production":
        logger.debug("[local] forgewing_api %s %s", method, path)
        return {"mock": True, "url": url, "method": method, "status": 200}
    try:
        headers: dict[str, str] = {}
        key = _get_api_key()
        if key:
            headers["X-API-Key"] = key
        kwargs: dict[str, Any] = {"timeout": timeout, "headers": headers}
        if data:
            kwargs["json"] = data
        resp = httpx.request(method, url, **kwargs)
        if resp.status_code in (401, 403):
            # Stale key — invalidate and retry once with a fresh fetch
            _invalidate_key_cache()
            key2 = _get_api_key()
            if key2 and key2 != key:
                headers["X-API-Key"] = key2
                resp = httpx.request(method, url, **kwargs)
        if resp.status_code >= 400:
            return {"error": resp.text[:200], "status": resp.status_code, "url": url}
        try:
            return resp.json()
        except Exception:
            return {"text": resp.text[:500], "status": resp.status_code, "url": url}
    except Exception as exc:
        logger.warning("forgewing_api %s %s failed: %s", method, path, exc)
        return {"error": str(exc), "url": url}


def get_health() -> dict[str, Any]:
    """GET /health — quick liveness check for the Forgewing API."""
    return call_api("GET", "/health")


def get_tenant_status(tenant_id: str) -> dict[str, Any]:
    """GET /api/status/{tenant_id} — tenant summary from Forgewing."""
    return call_api("GET", f"/api/status/{tenant_id}")


def verify_onboarding(tenant_id: str) -> dict[str, Any]:
    """GET /onboarding/verify/{tenant_id} — onboarding checklist."""
    if MODE != "production":
        return {
            "tenant_id": tenant_id,
            "mock": True,
            "checks": {
                "tenant_exists": True,
                "token_present": True,
                "write_access": True,
                "repo_indexed": True,
                "tasks_created": True,
            },
        }
    return call_api("GET", f"/onboarding/verify/{tenant_id}")


def retrigger_ingestion(tenant_id: str) -> dict[str, Any]:
    """POST /reingest/{tenant_id} — re-ingest the tenant's repo."""
    return call_api("POST", f"/reingest/{tenant_id}")
