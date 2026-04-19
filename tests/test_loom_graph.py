"""Tests for Loom v0 Neptune wrapper."""
from __future__ import annotations
from unittest.mock import patch
import pytest

from nexus.ontology import graph
from nexus.ontology.exceptions import GraphWriteError, ObjectNotFoundError, TenantMismatchError
from nexus.ontology.schema import Feature


def _feature(**overrides):
    d = dict(id="feat-1", tenant_id="forge-t", version_id=1,
             created_at="2026-04-20T12:00:00+00:00", updated_at="2026-04-20T12:00:00+00:00",
             created_by="founder-1", object_type="Feature", project_id="proj-p",
             name="Login", description="Email+password")
    d.update(overrides)
    return Feature(**d)


class TestMergeObject:
    @patch.object(graph, "query")
    def test_success(self, mock_q):
        mock_q.return_value = [{"id": "feat-1", "version_id": 1}]
        out = graph.merge_object(_feature())
        assert out == {"id": "feat-1", "version_id": 1}
        cypher, params = mock_q.call_args.args
        assert "MERGE (n:Feature" in cypher
        assert params["props"]["name"] == "Login"

    @patch.object(graph, "query")
    def test_empty_response(self, mock_q):
        mock_q.return_value = []
        assert graph.merge_object(_feature()) == {"id": "feat-1", "version_id": 1}

    @patch.object(graph, "query")
    def test_exception_wrapped(self, mock_q):
        mock_q.side_effect = RuntimeError("down")
        with pytest.raises(GraphWriteError, match="MERGE failed"):
            graph.merge_object(_feature())


class TestReadObject:
    @patch.object(graph, "query")
    def test_success(self, mock_q):
        mock_q.return_value = [{"props": {"id": "feat-1", "tenant_id": "forge-t", "name": "Login"}}]
        assert graph.read_object("Feature", "feat-1", "forge-t")["name"] == "Login"

    @patch.object(graph, "query")
    def test_not_found(self, mock_q):
        mock_q.return_value = []
        with pytest.raises(ObjectNotFoundError):
            graph.read_object("Feature", "missing", "forge-t")

    @patch.object(graph, "query")
    def test_tenant_mismatch(self, mock_q):
        mock_q.return_value = [{"props": {"id": "feat-1", "tenant_id": "other"}}]
        with pytest.raises(TenantMismatchError):
            graph.read_object("Feature", "feat-1", "forge-t")

    def test_unknown_label(self):
        with pytest.raises(GraphWriteError, match="unknown label"):
            graph.read_object("NotAType", "x", "forge-t")
