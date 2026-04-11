"""
Tests for the diagnostic report and Ops chat endpoints.

Both must work in NEXUS_MODE=local without making real Bedrock or
AWS calls. The Ops chat returns a stub message in local mode; the
diagnostic report renders against the existing local-mode mocks.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus.server import app  # noqa: E402

client = TestClient(app)


def test_diagnostic_report_returns_text():
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    body = resp.json()
    assert "report" in body
    assert "generated_at" in body
    text = body["report"]
    assert text.startswith("OVERWATCH DIAGNOSTIC")
    assert "Platform:" in text
    assert "Daemon:" in text
    assert "CI:" in text
    assert "Tenants:" in text
    # Mock tenants in local mode
    assert "TENANT" in text and "tenant-alpha" in text
    assert "Paste this into Claude" in text


def test_diagnostic_report_includes_actions_section_when_present():
    # Trigger an action so the registry has something to report
    from nexus.capabilities.registry import registry

    registry.execute("get_service_logs", cluster="aria-platform", service="aria-daemon")

    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    text = resp.json()["report"]
    assert "RECENT ACTIONS:" in text
    assert "get_service_logs" in text


def test_ops_chat_local_mode_returns_stub():
    resp = client.post("/api/ops/chat", json={"message": "why is the daemon stale?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "response" in body
    assert "[Local mode]" in body["response"]
    assert body["mode"] == "local"
    assert body["model"]  # should echo configured model id


def test_ops_chat_rejects_empty_message():
    resp = client.post("/api/ops/chat", json={"message": ""})
    assert resp.status_code == 400


def test_ops_chat_rejects_missing_message():
    resp = client.post("/api/ops/chat", json={})
    assert resp.status_code == 400


def test_ops_chat_default_model_is_sonnet_4_6():
    """Catch accidental downgrades of the default model id."""
    from nexus.config import OPS_CHAT_MODEL_ID

    assert "sonnet-4-6" in OPS_CHAT_MODEL_ID or OPS_CHAT_MODEL_ID.endswith(
        "claude-sonnet-4-6"
    )
