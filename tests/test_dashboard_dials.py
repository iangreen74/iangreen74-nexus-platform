"""Tests for dial data in the /status polling endpoint."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus.server import app  # noqa: E402

client = TestClient(app)

DIAL_KEYS = ["response_time_ms", "error_rate_pct", "uptime_pct", "daemon_cycle_pct", "ci_green_pct"]


def test_dials_present_in_status_response():
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "dials" in data
    dials = data["dials"]
    for key in DIAL_KEYS:
        assert key in dials, f"missing dial: {key}"


def test_dial_values_are_numeric():
    resp = client.get("/api/status")
    dials = resp.json()["dials"]
    for k, v in dials.items():
        assert v is None or isinstance(v, (int, float)), f"{k} is not numeric: {v}"


def test_findings_summary_present():
    resp = client.get("/api/status")
    data = resp.json()
    assert "findings_summary" in data
    fs = data["findings_summary"]
    for key in ["total", "critical", "warning", "info"]:
        assert key in fs, f"missing findings key: {key}"
        assert isinstance(fs[key], int), f"{key} should be int"
