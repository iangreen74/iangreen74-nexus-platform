"""Tests for the root `/` route redirecting to `/engineering`.

The legacy V1 OVERWATCH dashboard at `nexus/dashboard/static/index.html`
was previously served at `/`. After Cognito sign-out the redirect chain
landed operators on that dead-code page. The `/` route now bounces to
`/engineering` (the V2 React app surface) so sign-out always lands on
the correct UI.
"""
from __future__ import annotations

import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from nexus.server import app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_root_redirects_to_engineering(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/engineering"


def test_root_redirect_does_not_serve_legacy_html(client: TestClient) -> None:
    """Body must not be the V1 OVERWATCH HTML — only an empty 302."""
    response = client.get("/", follow_redirects=False)
    assert "OVERWATCH" not in response.text
    assert "<!DOCTYPE html>" not in response.text
