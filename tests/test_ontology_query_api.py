"""Tests for Loom query endpoints."""
from __future__ import annotations
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nexus.ontology.query_api import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/ontology")
    return TestClient(app)


def test_summary_empty(client):
    with patch("nexus.ontology.query_api._query", return_value=[]):
        r = client.get("/api/ontology/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["tenants"] == []
    assert body["totals"] == {"objects": 0, "links": 0}
    assert "Feature" in body["object_types"]


def test_summary_one_tenant(client):
    def fake(cypher, params=None):
        if ":Feature)" in cypher:
            return [{"tid": "forge-t", "n": 3}]
        return []
    with patch("nexus.ontology.query_api._query", side_effect=fake):
        r = client.get("/api/ontology/summary")
    body = r.json()
    assert len(body["tenants"]) == 1
    assert body["tenants"][0]["objects"]["Feature"] == 3
    assert body["totals"]["objects"] == 3


def test_summary_multi_tenant_multi_type(client):
    def fake(cypher, params=None):
        if ":Feature)" in cypher:
            return [{"tid": "a", "n": 2}, {"tid": "b", "n": 1}]
        if ":Decision)" in cypher:
            return [{"tid": "a", "n": 1}]
        if ":motivates]" in cypher:
            return [{"tid": "a", "n": 2}]
        return []
    with patch("nexus.ontology.query_api._query", side_effect=fake):
        body = client.get("/api/ontology/summary").json()
    by = {t["tenant_id"]: t for t in body["tenants"]}
    assert by["a"]["objects"]["Feature"] == 2
    assert by["a"]["links"]["motivates"] == 2
    assert by["a"]["total_objects"] == 3
    assert body["totals"]["objects"] == 4


def test_summary_neptune_failure(client):
    with patch("nexus.ontology.query_api.neptune_client.query", side_effect=RuntimeError):
        r = client.get("/api/ontology/summary")
    assert r.status_code == 200
    assert r.json()["tenants"] == []


def test_objects_empty(client):
    with patch("nexus.ontology.query_api._query", return_value=[]):
        r = client.get("/api/ontology/tenants/forge-t/objects")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_objects_type_filter(client):
    calls = []
    def fake(cypher, params=None):
        calls.append(cypher)
        return []
    with patch("nexus.ontology.query_api._query", side_effect=fake):
        client.get("/api/ontology/tenants/forge-t/objects?type=Feature")
    assert all(":Feature)" in c for c in calls)


def test_objects_invalid_type(client):
    r = client.get("/api/ontology/tenants/forge-t/objects?type=Bogus")
    assert r.status_code == 400


def test_objects_limit_bounds(client):
    assert client.get("/api/ontology/tenants/t/objects?limit=0").status_code == 422
    assert client.get("/api/ontology/tenants/t/objects?limit=500").status_code == 422


def test_objects_shape(client):
    def fake(cypher, params=None):
        if ":Feature)" in cypher:
            return [{"object_id": "f1", "title": "auth", "created_at": "2026-04-19",
                     "updated_at": "2026-04-19", "version_id": 1}]
        return []
    with patch("nexus.ontology.query_api._query", side_effect=fake):
        body = client.get("/api/ontology/tenants/t/objects?type=Feature").json()
    assert body["count"] == 1
    assert body["objects"][0]["object_type"] == "Feature"
    assert body["objects"][0]["title"] == "auth"
