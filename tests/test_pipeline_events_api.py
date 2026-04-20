"""Tests for Pipeline Events query endpoint."""
from __future__ import annotations
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nexus.dashboard.pipeline_events_api import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_empty(client):
    with patch("nexus.dashboard.pipeline_events_api._query", return_value=[]):
        r = client.get("/api/pipeline-events")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["events"] == []
    assert body["type_counts"] == {}


def test_returns_events(client):
    rows = [{"event_id": "e1", "event_type": "ci_deploy_completed",
             "emitted_at": "2026-04-20T02:33:00Z", "tenant_id": "forge-self",
             "project_id": "p1", "correlation_id": "ci-run-1",
             "recorded_at": "2026-04-20T02:34:00Z"}]
    with patch("nexus.dashboard.pipeline_events_api._query", return_value=rows):
        body = client.get("/api/pipeline-events").json()
    assert body["count"] == 1
    assert body["events"][0]["event_id"] == "e1"
    assert body["type_counts"] == {"ci_deploy_completed": 1}


def test_filter_event_type(client):
    captured = {}
    def fake(cypher, params=None):
        captured.update(cypher=cypher, params=params)
        return []
    with patch("nexus.dashboard.pipeline_events_api._query", side_effect=fake):
        client.get("/api/pipeline-events?event_type=ci_deploy_failed")
    assert "e.event_type = $event_type" in captured["cypher"]
    assert captured["params"]["event_type"] == "ci_deploy_failed"


def test_invalid_event_type_400(client):
    assert client.get("/api/pipeline-events?event_type=bogus").status_code == 400


def test_filter_tenant(client):
    captured = {}
    def fake(cypher, params=None):
        captured.update(cypher=cypher, params=params)
        return []
    with patch("nexus.dashboard.pipeline_events_api._query", side_effect=fake):
        client.get("/api/pipeline-events?tenant_id=forge-self")
    assert "e.tenant_id = $tenant_id" in captured["cypher"]


def test_filter_since(client):
    captured = {}
    def fake(cypher, params=None):
        captured.update(cypher=cypher, params=params)
        return []
    with patch("nexus.dashboard.pipeline_events_api._query", side_effect=fake):
        client.get("/api/pipeline-events?since=2026-04-20T00:00:00Z")
    assert "e.emitted_at >= $since" in captured["cypher"]


def test_combined_filters(client):
    captured = {}
    def fake(cypher, params=None):
        captured.update(cypher=cypher, params=params)
        return []
    with patch("nexus.dashboard.pipeline_events_api._query", side_effect=fake):
        client.get("/api/pipeline-events?event_type=ci_gate_overridden&tenant_id=t&since=2026-01-01&limit=50")
    assert "AND" in captured["cypher"]
    assert captured["params"]["limit"] == 50


def test_type_counts(client):
    rows = [
        {"event_id": "e1", "event_type": "ci_deploy_completed"},
        {"event_id": "e2", "event_type": "ci_deploy_completed"},
        {"event_id": "e3", "event_type": "ci_deploy_failed"},
    ]
    with patch("nexus.dashboard.pipeline_events_api._query", return_value=rows):
        body = client.get("/api/pipeline-events").json()
    assert body["type_counts"] == {"ci_deploy_completed": 2, "ci_deploy_failed": 1}


def test_limit_bounds(client):
    assert client.get("/api/pipeline-events?limit=0").status_code == 422
    assert client.get("/api/pipeline-events?limit=1000").status_code == 422


def test_neptune_failure_empty(client):
    with patch("nexus.dashboard.pipeline_events_api.neptune_client.query",
               side_effect=RuntimeError):
        r = client.get("/api/pipeline-events")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_known_types_in_response(client):
    with patch("nexus.dashboard.pipeline_events_api._query", return_value=[]):
        body = client.get("/api/pipeline-events").json()
    assert "ci_deploy_completed" in body["known_event_types"]
    assert len(body["known_event_types"]) == 5
