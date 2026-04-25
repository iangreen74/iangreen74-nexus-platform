"""Tests for the /oauth2/sign-out backend route (Track M).

Verifies the operator-facing sign-out endpoint redirects to Cognito's
hosted-UI logout and clears the ALB session cookies in the response.
"""
from __future__ import annotations

import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from nexus.server import (  # noqa: E402
    _ALB_AUTH_COOKIE_NAMES,
    _COGNITO_CLIENT_ID,
    _COGNITO_DOMAIN,
    _LOGOUT_URI,
    app,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_sign_out_redirects_to_cognito_logout(client: TestClient) -> None:
    response = client.get("/oauth2/sign-out", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert _COGNITO_DOMAIN in location, location
    assert f"client_id={_COGNITO_CLIENT_ID}" in location, location
    assert f"logout_uri={_LOGOUT_URI}" in location, location
    assert "/logout?" in location, location


def test_sign_out_clears_alb_session_cookies(client: TestClient) -> None:
    response = client.get("/oauth2/sign-out", follow_redirects=False)
    set_cookie = " | ".join(response.headers.get_list("set-cookie"))
    for name in _ALB_AUTH_COOKIE_NAMES:
        assert name in set_cookie, f"missing cookie clear for {name}: {set_cookie}"
    # delete_cookie emits Max-Age=0; that's the marker that tells the
    # browser to expire the cookie.
    assert "Max-Age=0" in set_cookie or 'expires=' in set_cookie.lower(), set_cookie


def test_sign_out_cookie_attributes_match_alb(client: TestClient) -> None:
    """Browser only deletes a cookie when the deletion's Path matches
    the original. ALB sets session cookies with Path=/, host-only, Secure,
    HttpOnly. We mirror those so the deletion actually applies."""
    response = client.get("/oauth2/sign-out", follow_redirects=False)
    cookies = response.headers.get_list("set-cookie")
    for raw in cookies:
        if not any(name in raw for name in _ALB_AUTH_COOKIE_NAMES):
            continue
        assert "Path=/" in raw, raw
        assert "Secure" in raw, raw
        assert "HttpOnly" in raw, raw
        # Must not pin to a domain — ALB's cookies are host-only and
        # specifying a Domain attribute on deletion would target a
        # *different* cookie.
        assert "Domain=" not in raw, raw
