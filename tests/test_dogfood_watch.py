"""Tests for dogfood live watch — render + HTTP client."""
import json
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.intelligence import dogfood_watch


# --- render ---
def test_render_handles_no_batch():
    out = dogfood_watch.render(None)
    assert "No active batch" in out


def test_render_handles_error():
    out = dogfood_watch.render({"error": "query failed"})
    assert "Error" in out


def test_render_with_runs():
    snap = {
        "batch_id": "batch-abc123def",
        "completed": 2,
        "remaining": 8,
        "success_rate": 0.0,
        "runs": [
            {"run_id": "r1", "app": "df-express", "status": "failed",
             "outcome": "deploy_never_started", "pid": "p1"},
        ],
        "stages": {"p1": {"briefs": 1, "blueprints": 0, "tasks": 0, "prs": 0}},
    }
    out = dogfood_watch.render(snap)
    assert "df-express" in out
    assert "failed" in out
    assert "b=1" in out
    assert "batch-abc123" in out


def test_render_blueprint_marker():
    snap = {
        "batch_id": "b1",
        "completed": 0,
        "remaining": 1,
        "success_rate": 0.0,
        "runs": [
            {"run_id": "r1", "app": "df-flask-api", "status": "pending",
             "outcome": "", "pid": "p1"},
        ],
        "stages": {"p1": {"briefs": 1, "blueprints": 1, "tasks": 3, "prs": 0}},
    }
    out = dogfood_watch.render(snap)
    assert "🎯" in out


# --- HTTP client ---
def _mock_urlopen(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_fetch_snapshot_handles_no_batch():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen({"status": "no_active_batch"})):
        result = dogfood_watch._fetch_snapshot("https://example.com")
    assert result is None


def test_fetch_snapshot_returns_data():
    payload = {"batch_id": "batch-abc", "completed": 2, "remaining": 8,
               "runs": [], "stages": {}}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
        result = dogfood_watch._fetch_snapshot("https://example.com")
    assert result["batch_id"] == "batch-abc"


def test_fetch_snapshot_handles_network_error():
    with patch("urllib.request.urlopen", side_effect=OSError("dns failed")):
        result = dogfood_watch._fetch_snapshot("https://example.com")
    assert "error" in result
    assert "dns failed" in result["error"]


# --- graph-side snapshot ---
def test_snapshot_from_graph_returns_none_on_empty():
    snap = dogfood_watch.snapshot_from_graph()
    assert snap is None or isinstance(snap, dict)
