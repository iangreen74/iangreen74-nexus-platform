"""Unit tests for the dogfood runner. Runs entirely in NEXUS_MODE=local."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import dogfood_capability  # noqa: E402
from nexus.capabilities.dogfood_catalogue import CATALOGUE, pick_app  # noqa: E402
from nexus.sensors import dogfood_reconciler, dogfood_sensor  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_graph(monkeypatch):
    """Clean slate for local store before each test."""
    overwatch_graph._local_store["OverwatchDogfoodRun"] = []
    overwatch_graph._local_store["OverwatchDogfoodCursor"] = []
    monkeypatch.setenv("DOGFOOD_ENABLED", "true")
    monkeypatch.setenv("DOGFOOD_TENANT_ID", "forge-test")
    yield
    overwatch_graph._local_store["OverwatchDogfoodRun"] = []
    overwatch_graph._local_store["OverwatchDogfoodCursor"] = []


# --- catalogue shape -------------------------------------------------------
def test_catalogue_shape():
    assert len(CATALOGUE) == 3, "start narrow — 3 confirmed-working apps"
    for app in CATALOGUE:
        assert set(app.keys()) >= {"name", "desc", "fingerprint", "files"}
        assert app["name"].startswith("df-")
        files = app["files"]
        assert "Dockerfile" in files, f"{app['name']} missing Dockerfile"
        assert len(files) == 3, f"{app['name']} should have exactly 3 files"
        for path, content in files.items():
            assert isinstance(content, str) and content.strip(), f"{app['name']}/{path} empty"


def test_pick_app_wraps_around():
    n = len(CATALOGUE)
    assert pick_app(0)["name"] == CATALOGUE[0]["name"]
    assert pick_app(n)["name"] == CATALOGUE[0]["name"]
    assert pick_app(n + 1)["name"] == CATALOGUE[1]["name"]
    assert pick_app(100 * n + 2)["name"] == CATALOGUE[2]["name"]


# --- cursor advancement ---------------------------------------------------
def test_cursor_starts_at_zero_and_advances():
    assert overwatch_graph.get_dogfood_cursor() == 0
    assert overwatch_graph.advance_dogfood_cursor() == 1
    assert overwatch_graph.get_dogfood_cursor() == 1
    assert overwatch_graph.advance_dogfood_cursor() == 2
    assert overwatch_graph.advance_dogfood_cursor() == 3


def test_cursor_drives_catalogue_walk():
    # After N advances we walk the catalogue once.
    seen = []
    for _ in range(len(CATALOGUE)):
        seen.append(pick_app(overwatch_graph.get_dogfood_cursor())["name"])
        overwatch_graph.advance_dogfood_cursor()
    assert sorted(seen) == sorted(a["name"] for a in CATALOGUE)


# --- circuit breaker ------------------------------------------------------
def _seed_terminal_runs(count: int, successes: int):
    """Drop `count` runs into the local store: first `successes` succeed."""
    now = datetime.now(timezone.utc)
    for i in range(count):
        status = "success" if i < successes else "failed"
        overwatch_graph._local_store["OverwatchDogfoodRun"].append({
            "id": f"run-{i}",
            "app_name": "df-flask-api",
            "fingerprint": "python/flask",
            "status": status,
            "started_at": (now - timedelta(minutes=count - i)).isoformat(),
            "completed_at": (now - timedelta(minutes=count - i - 1)).isoformat(),
            "repo_name": f"df-repo-{i}",
            "project_id": f"proj-{i}",
            "tenant_id": "forge-test",
            "cleaned_up": "",
        })


def test_circuit_breaker_opens_on_all_failures():
    _seed_terminal_runs(10, successes=0)
    assert dogfood_capability.circuit_open() is True


def test_circuit_breaker_opens_on_single_success():
    _seed_terminal_runs(10, successes=1)
    assert dogfood_capability.circuit_open() is True


def test_circuit_breaker_closed_with_enough_successes():
    _seed_terminal_runs(10, successes=5)
    assert dogfood_capability.circuit_open() is False


def test_circuit_breaker_closed_with_few_runs():
    # Fewer than CIRCUIT_WINDOW terminal runs → never open.
    _seed_terminal_runs(5, successes=0)
    assert dogfood_capability.circuit_open() is False


def test_circuit_breaker_suppresses_new_run():
    _seed_terminal_runs(10, successes=0)
    result = dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    assert result.get("skipped") is True
    assert result.get("reason") == "circuit_open"


def test_disabled_env_skips_run(monkeypatch):
    monkeypatch.setenv("DOGFOOD_ENABLED", "false")
    result = dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    assert result.get("skipped") is True
    assert "not enabled" in result.get("reason", "")


# --- kickoff (local mode) -------------------------------------------------
def test_run_in_local_mode_records_pending_and_advances_cursor():
    result = dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    assert result.get("status") == "kicked_off"
    runs = overwatch_graph.list_dogfood_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "pending"
    assert runs[0]["tenant_id"] == "forge-test"
    assert overwatch_graph.get_dogfood_cursor() == 1


# --- sensor ---------------------------------------------------------------
def test_sensor_marks_success_on_live_stage():
    dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    with patch.object(dogfood_sensor.forgewing_api, "call_api",
                      return_value={"stage": "live"}):
        report = dogfood_sensor.check_dogfood_runs()
    assert report["completed"] == 1
    runs = overwatch_graph.list_dogfood_runs()
    assert runs[0]["status"] == "success"
    assert runs[0]["completed_at"]


def test_sensor_marks_failed_on_failed_stage():
    dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    with patch.object(dogfood_sensor.forgewing_api, "call_api",
                      return_value={"stage": "failed", "message": "container exit 1"}):
        report = dogfood_sensor.check_dogfood_runs()
    assert report["failed"] == 1
    runs = overwatch_graph.list_dogfood_runs()
    assert runs[0]["status"] == "failed"


def test_sensor_timeouts_stale_pending(monkeypatch):
    dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    # Age the run past the timeout window.
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    overwatch_graph._local_store["OverwatchDogfoodRun"][0]["started_at"] = old
    monkeypatch.setenv("DOGFOOD_MAX_WAIT_MINUTES", "5")
    report = dogfood_sensor.check_dogfood_runs()
    assert report["timed_out"] == 1
    assert overwatch_graph.list_dogfood_runs()[0]["status"] == "timeout"


# --- inactivity classification -------------------------------------------
def _seed_not_started_run(batch_id="batch-test", progress_ago_min=None, dispatch_ago_min=10):
    """Create a pending dogfood run with optional last_progress_at."""
    dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    run = overwatch_graph._local_store["OverwatchDogfoodRun"][0]
    run["batch_id"] = batch_id
    now = datetime.now(timezone.utc)
    run["started_at"] = (now - timedelta(minutes=dispatch_ago_min)).isoformat()
    if progress_ago_min is not None:
        run["last_progress_at"] = (now - timedelta(minutes=progress_ago_min)).isoformat()
    return run


def test_sensor_not_stalled_with_recent_progress():
    _seed_not_started_run(progress_ago_min=5, dispatch_ago_min=60)
    with patch.object(dogfood_sensor.forgewing_api, "call_api",
                      return_value={"stage": "not_started"}):
        report = dogfood_sensor.check_dogfood_runs()
    assert report["failed"] == 0
    assert overwatch_graph.list_dogfood_runs()[0]["status"] == "pending"


def test_sensor_stalled_with_old_progress():
    _seed_not_started_run(progress_ago_min=25, dispatch_ago_min=60)
    with patch.object(dogfood_sensor.forgewing_api, "call_api",
                      return_value={"stage": "not_started"}):
        report = dogfood_sensor.check_dogfood_runs()
    assert report["failed"] == 1
    assert overwatch_graph.list_dogfood_runs()[0]["status"] == "failed"


def test_sensor_stalled_no_progress_old_dispatch():
    _seed_not_started_run(progress_ago_min=None, dispatch_ago_min=25)
    with patch.object(dogfood_sensor.forgewing_api, "call_api",
                      return_value={"stage": "not_started"}):
        report = dogfood_sensor.check_dogfood_runs()
    assert report["failed"] == 1


def test_sensor_not_stalled_no_progress_recent_dispatch():
    _seed_not_started_run(progress_ago_min=None, dispatch_ago_min=5)
    with patch.object(dogfood_sensor.forgewing_api, "call_api",
                      return_value={"stage": "not_started"}):
        report = dogfood_sensor.check_dogfood_runs()
    assert report["failed"] == 0
    assert overwatch_graph.list_dogfood_runs()[0]["status"] == "pending"


# --- reconciler -----------------------------------------------------------
def test_reconciler_marks_cleaned_for_old_failed_runs():
    dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    run = overwatch_graph._local_store["OverwatchDogfoodRun"][0]
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    run["status"] = "failed"
    run["completed_at"] = past
    report = dogfood_reconciler.reconcile_dogfood()
    assert report["cleaned"] == 1
    assert overwatch_graph.list_dogfood_runs()[0]["cleaned_up"]


def test_reconciler_preserves_successful_runs():
    dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    run = overwatch_graph._local_store["OverwatchDogfoodRun"][0]
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    run["status"] = "success"
    run["completed_at"] = past
    report = dogfood_reconciler.reconcile_dogfood()
    assert report["cleaned"] == 0


def test_reconciler_waits_for_grace_period():
    dogfood_capability.run_dogfood_cycle(tenant_id="forge-test")
    run = overwatch_graph._local_store["OverwatchDogfoodRun"][0]
    run["status"] = "success"
    # Completed just now — under the 10-min grace period.
    run["completed_at"] = datetime.now(timezone.utc).isoformat()
    report = dogfood_reconciler.reconcile_dogfood()
    assert report["cleaned"] == 0
    assert not overwatch_graph.list_dogfood_runs()[0]["cleaned_up"]
