"""Tests for Loom v0 service actions."""
from __future__ import annotations
from unittest.mock import patch
import pytest

from nexus.ontology import service
from nexus.ontology.exceptions import SchemaValidationError

FEAT = {"name": "Login", "description": "Email+password login flow"}


@pytest.fixture
def mock_graph():
    with patch.object(service.graph, "merge_object") as m_merge, \
         patch.object(service.graph, "read_object") as m_read:
        m_merge.side_effect = lambda obj: {"id": obj.id, "version_id": obj.version_id}
        yield {"merge": m_merge, "read": m_read}


class TestProposeObject:
    def test_creates_version_1(self, mock_graph):
        r = service.propose_object("Feature", "forge-t", FEAT, "f", project_id="p")
        assert r["version_id"] == 1
        assert r["object_id"]
        assert r["action_event_id"]

    def test_unique_ids(self, mock_graph):
        a = service.propose_object("Feature", "forge-t", FEAT, "f", project_id="p")
        b = service.propose_object("Feature", "forge-t", FEAT, "f", project_id="p")
        assert a["object_id"] != b["object_id"]

    def test_writes_to_neptune(self, mock_graph):
        service.propose_object("Feature", "forge-t", FEAT, "founder", project_id="p")
        obj = mock_graph["merge"].call_args.args[0]
        assert obj.object_type == "Feature"
        assert obj.name == "Login"

    def test_invalid_type_raises(self, mock_graph):
        with pytest.raises(SchemaValidationError, match="object_type"):
            service.propose_object("NotAType", "forge-t", {}, "f")

    def test_missing_field_raises(self, mock_graph):
        with pytest.raises(SchemaValidationError, match="description"):
            service.propose_object("Feature", "forge-t", {"name": "X"}, "f", project_id="p")

    def test_empty_tenant_raises(self, mock_graph):
        with pytest.raises(SchemaValidationError, match="tenant_id"):
            service.propose_object("Feature", "", FEAT, "f", project_id="p")

    def test_empty_actor_raises(self, mock_graph):
        with pytest.raises(SchemaValidationError, match="actor"):
            service.propose_object("Feature", "forge-t", FEAT, "", project_id="p")


class TestUpdateObject:
    def _current(self):
        return dict(
            id="feat-1", tenant_id="forge-t", project_id="proj-p",
            object_type="Feature", version_id=1,
            created_at="2026-04-20T12:00:00+00:00", updated_at="2026-04-20T12:00:00+00:00",
            created_by="founder-1", name="Login", description="Original", status="proposed",
        )

    def test_increments_version(self, mock_graph):
        mock_graph["read"].return_value = self._current()
        r = service.update_object("Feature", "feat-1", "forge-t",
                                  {"status": "in_progress"}, "f", "Starting")
        assert r["version_id"] == 2

    def test_merges_properties(self, mock_graph):
        mock_graph["read"].return_value = self._current()
        service.update_object("Feature", "feat-1", "forge-t",
                              {"description": "Updated"}, "f", "Clarify")
        obj = mock_graph["merge"].call_args.args[0]
        assert obj.description == "Updated"
        assert obj.name == "Login"

    def test_invalid_merged_state_raises(self, mock_graph):
        mock_graph["read"].return_value = self._current()
        with pytest.raises(SchemaValidationError, match="status"):
            service.update_object("Feature", "feat-1", "forge-t",
                                  {"status": "bogus"}, "f", "Test")

    def test_missing_reason_raises(self, mock_graph):
        with pytest.raises(SchemaValidationError, match="change_reason"):
            service.update_object("Feature", "feat-1", "forge-t", {}, "f", "")

    def test_id_immutable(self, mock_graph):
        mock_graph["read"].return_value = self._current()
        service.update_object("Feature", "feat-1", "forge-t",
                              {"id": "hijacked", "description": "x"}, "f", "attempt")
        obj = mock_graph["merge"].call_args.args[0]
        assert obj.id == "feat-1"
        assert obj.tenant_id == "forge-t"
