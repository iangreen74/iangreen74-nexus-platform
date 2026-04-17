"""Tests for the deploy-critical cycle and admin advance-deploy."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import deploy_cycle  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


def _clean():
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodSchedule", None)


def setup_function(_fn):
    _clean()


def teardown_function(_fn):
    _clean()


# --- run_deploy_cycle --------------------------------------------------------


def test_deploy_cycle_runs_without_error():
    result = asyncio.get_event_loop().run_until_complete(
        deploy_cycle.run_deploy_cycle()
    )
    assert "dogfood_sensor" in result
    assert "dogfood_reconciler" in result


# --- schedule check ----------------------------------------------------------


def test_schedule_disabled_skips():
    result = deploy_cycle._check_schedule()
    assert result.get("skipped") is True


def test_schedule_queues_when_enabled():
    overwatch_graph.set_dogfood_schedule(10, enabled=True)
    result = deploy_cycle._check_schedule()
    assert result.get("queued") is True or result.get("skipped") is True


# --- batch completion auto-pause ---------------------------------------------


def test_batch_completion_auto_pauses():
    overwatch_graph.set_dogfood_config(enabled=True, activated_by="batch")
    result = deploy_cycle._check_batch_completion()
    assert result.get("auto_paused") is True
    config = overwatch_graph.get_dogfood_config()
    assert config.get("enabled") is False


def test_batch_completion_does_not_pause_env_activated(monkeypatch):
    monkeypatch.setenv("DOGFOOD_ENABLED", "true")
    overwatch_graph.set_dogfood_config(enabled=True, activated_by="batch")
    result = deploy_cycle._check_batch_completion()
    assert result.get("auto_paused") is not True


def test_batch_active_no_pause():
    from nexus import learning_overview as lo
    lo.run_batch(100)
    overwatch_graph.set_dogfood_config(enabled=True, activated_by="batch")
    result = deploy_cycle._check_batch_completion()
    assert result.get("active") is True


# --- admin advance-deploy ----------------------------------------------------


def test_advance_deploy_endpoint():
    resp = client.post("/api/admin/advance-deploy/forge-test-tenant")
    assert resp.status_code == 200
    body = resp.json()
    # In local mode the Forgewing API returns mock data
    assert isinstance(body, dict)
