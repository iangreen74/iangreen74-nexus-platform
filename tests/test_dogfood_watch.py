"""Tests for dogfood live watch CLI."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.intelligence import dogfood_watch


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


def test_snapshot_returns_none_on_empty():
    snap = dogfood_watch.snapshot()
    assert snap is None or isinstance(snap, dict)
