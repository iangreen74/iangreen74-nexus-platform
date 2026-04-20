"""Tests for Loom v0 API routes."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus.ontology import routes
from nexus.ontology.exceptions import (
    ObjectNotFoundError,
    SchemaValidationError,
    TenantMismatchError,
)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/ontology")
    return TestClient(app)


@pytest.fixture
def mock_service():
    with patch.object(routes.service, "propose_object") as m_propose, \
         patch.object(routes.service, "update_object") as m_update:
        yield {"propose": m_propose, "update": m_update}


PROPOSE = {
    "object_type": "Feature",
    "tenant_id": "forge-t",
    "project_id": "proj-p",
    "actor": "founder-1",
    "properties": {"name": "Login", "description": "Email+password"},
}

UPDATE = {
    "object_type": "Feature",
    "object_id": "feat-1",
    "tenant_id": "forge-t",
    "actor": "founder-1",
    "change_reason": "Starting implementation",
    "updated_properties": {"status": "in_progress"},
}


class TestProposeObject:
    def test_success(self, client, mock_service):
        mock_service["propose"].return_value = {
            "object_id": "feat-new", "version_id": 1, "action_event_id": "evt",
        }
        r = client.post("/api/ontology/propose_object", json=PROPOSE)
        assert r.status_code == 200
        assert r.json() == {"object_id": "feat-new", "version_id": 1, "action_event_id": "evt"}

    def test_delegates_all_fields(self, client, mock_service):
        mock_service["propose"].return_value = {"object_id": "x", "version_id": 1, "action_event_id": "y"}
        body = {**PROPOSE, "source_conversation_id": "conv-1"}
        client.post("/api/ontology/propose_object", json=body)
        kw = mock_service["propose"].call_args.kwargs
        assert kw["object_type"] == "Feature"
        assert kw["tenant_id"] == "forge-t"
        assert kw["project_id"] == "proj-p"
        assert kw["source_conversation_id"] == "conv-1"
        assert kw["properties"]["name"] == "Login"

    def test_missing_object_type_400(self, client, mock_service):
        body = {k: v for k, v in PROPOSE.items() if k != "object_type"}
        assert client.post("/api/ontology/propose_object", json=body).status_code == 400
        mock_service["propose"].assert_not_called()

    def test_missing_tenant_id_400(self, client, mock_service):
        body = {k: v for k, v in PROPOSE.items() if k != "tenant_id"}
        assert client.post("/api/ontology/propose_object", json=body).status_code == 400

    def test_missing_actor_400(self, client, mock_service):
        body = {k: v for k, v in PROPOSE.items() if k != "actor"}
        assert client.post("/api/ontology/propose_object", json=body).status_code == 400

    def test_properties_must_be_dict(self, client, mock_service):
        body = {**PROPOSE, "properties": "not a dict"}
        r = client.post("/api/ontology/propose_object", json=body)
        assert r.status_code == 400
        assert "properties" in r.json()["detail"]

    def test_schema_error_400(self, client, mock_service):
        mock_service["propose"].side_effect = SchemaValidationError("desc required")
        r = client.post("/api/ontology/propose_object", json=PROPOSE)
        assert r.status_code == 400
        assert "desc" in r.json()["detail"]

    def test_empty_body_400(self, client, mock_service):
        assert client.post("/api/ontology/propose_object", json={}).status_code == 400


class TestUpdateObject:
    def test_success(self, client, mock_service):
        mock_service["update"].return_value = {
            "object_id": "feat-1", "version_id": 2, "action_event_id": "evt",
        }
        r = client.post("/api/ontology/update_object", json=UPDATE)
        assert r.status_code == 200
        assert r.json()["version_id"] == 2

    def test_delegates_all_fields(self, client, mock_service):
        mock_service["update"].return_value = {"object_id": "f", "version_id": 2, "action_event_id": "e"}
        client.post("/api/ontology/update_object", json=UPDATE)
        kw = mock_service["update"].call_args.kwargs
        assert kw["object_id"] == "feat-1"
        assert kw["change_reason"] == "Starting implementation"
        assert kw["updated_properties"]["status"] == "in_progress"

    def test_missing_object_id_400(self, client, mock_service):
        body = {k: v for k, v in UPDATE.items() if k != "object_id"}
        assert client.post("/api/ontology/update_object", json=body).status_code == 400

    def test_missing_change_reason_400(self, client, mock_service):
        body = {k: v for k, v in UPDATE.items() if k != "change_reason"}
        assert client.post("/api/ontology/update_object", json=body).status_code == 400

    def test_updated_properties_must_be_dict(self, client, mock_service):
        body = {**UPDATE, "updated_properties": ["not", "a", "dict"]}
        assert client.post("/api/ontology/update_object", json=body).status_code == 400

    def test_not_found_404(self, client, mock_service):
        mock_service["update"].side_effect = ObjectNotFoundError("No Feature")
        assert client.post("/api/ontology/update_object", json=UPDATE).status_code == 404

    def test_tenant_mismatch_403(self, client, mock_service):
        mock_service["update"].side_effect = TenantMismatchError("wrong tenant")
        assert client.post("/api/ontology/update_object", json=UPDATE).status_code == 403

    def test_schema_error_on_merged_state_400(self, client, mock_service):
        mock_service["update"].side_effect = SchemaValidationError("invalid status")
        assert client.post("/api/ontology/update_object", json=UPDATE).status_code == 400


class TestRoutingIntegrity:
    def test_propose_post_only(self, client):
        assert client.get("/api/ontology/propose_object").status_code == 405

    def test_update_post_only(self, client):
        assert client.get("/api/ontology/update_object").status_code == 405

    def test_unknown_endpoint_404(self, client):
        assert client.post("/api/ontology/nonexistent", json={}).status_code == 404
