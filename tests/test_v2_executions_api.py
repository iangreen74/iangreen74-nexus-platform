"""Tests for v2 Step Functions execution API."""
from __future__ import annotations
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from fastapi import FastAPI
    from nexus.dashboard.v2_executions_api import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_sfn():
    from nexus.dashboard import v2_executions_api
    v2_executions_api._sfn_client = None
    yield
    v2_executions_api._sfn_client = None


def test_list_empty(client):
    m = MagicMock()
    m.list_executions.return_value = {"executions": []}
    with patch("nexus.dashboard.v2_executions_api._sfn", return_value=m):
        r = client.get("/api/v2-executions")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_list_with_results(client):
    start = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    stop = datetime(2026, 4, 20, 12, 5, 30, tzinfo=timezone.utc)
    m = MagicMock()
    m.list_executions.return_value = {"executions": [{
        "executionArn": "arn:exec:1", "name": "att-1",
        "status": "SUCCEEDED", "startDate": start, "stopDate": stop,
    }]}
    with patch("nexus.dashboard.v2_executions_api._sfn", return_value=m):
        body = client.get("/api/v2-executions").json()
    assert body["count"] == 1
    assert body["executions"][0]["status"] == "SUCCEEDED"
    assert body["executions"][0]["duration_ms"] == 330000


def test_list_status_filter_valid(client):
    captured = {}
    def call(**kw):
        captured.update(kw)
        return {"executions": []}
    m = MagicMock()
    m.list_executions.side_effect = call
    with patch("nexus.dashboard.v2_executions_api._sfn", return_value=m):
        client.get("/api/v2-executions?status_filter=RUNNING")
    assert captured["statusFilter"] == "RUNNING"


def test_list_status_filter_invalid(client):
    assert client.get("/api/v2-executions?status_filter=BOGUS").status_code == 400


def test_list_limit_bounds(client):
    assert client.get("/api/v2-executions?limit=0").status_code == 422
    assert client.get("/api/v2-executions?limit=500").status_code == 422


def test_list_state_machine_not_found(client):
    m = MagicMock()
    m.list_executions.side_effect = ClientError(
        {"Error": {"Code": "StateMachineDoesNotExist", "Message": "nope"}},
        "ListExecutions")
    with patch("nexus.dashboard.v2_executions_api._sfn", return_value=m):
        body = client.get("/api/v2-executions").json()
    assert body["count"] == 0
    assert "StateMachineDoesNotExist" in body["error"]


def test_list_generic_error(client):
    m = MagicMock()
    m.list_executions.side_effect = RuntimeError("timeout")
    with patch("nexus.dashboard.v2_executions_api._sfn", return_value=m):
        body = client.get("/api/v2-executions").json()
    assert body["count"] == 0


def test_history_returns_events(client):
    ts = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    m = MagicMock()
    m.get_execution_history.return_value = {"events": [
        {"id": 1, "type": "ExecutionStarted", "timestamp": ts},
        {"id": 2, "type": "TaskStateEntered", "timestamp": ts,
         "stateEnteredEventDetails": {"name": "InitializeAttempt"}},
        {"id": 3, "type": "TaskFailed", "timestamp": ts,
         "taskFailedEventDetails": {"error": "States.TaskFailed"}},
    ]}
    with patch("nexus.dashboard.v2_executions_api._sfn", return_value=m):
        body = client.get("/api/v2-executions/arn:exec:1/history").json()
    assert body["count"] == 3
    assert body["events"][1]["state_entered"] == "InitializeAttempt"
    assert body["events"][2]["task_failed"] == "States.TaskFailed"


def test_history_404_missing(client):
    m = MagicMock()
    m.get_execution_history.side_effect = ClientError(
        {"Error": {"Code": "ExecutionDoesNotExist", "Message": "gone"}},
        "GetExecutionHistory")
    with patch("nexus.dashboard.v2_executions_api._sfn", return_value=m):
        assert client.get("/api/v2-executions/arn:x/history").status_code == 404


def test_history_limit_bounds(client):
    assert client.get("/api/v2-executions/a/history?limit=0").status_code == 422
    assert client.get("/api/v2-executions/a/history?limit=5000").status_code == 422
