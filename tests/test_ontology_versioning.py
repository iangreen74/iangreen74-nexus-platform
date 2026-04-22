"""Tests for ontology Postgres-first write invariant.

Sprint 13 Day 1 B5-prov. Mocks both backends — no real Postgres needed.
"""
import os
os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import patch, MagicMock
import pytest

FEATURE_PROPS = {"name": "Login", "description": "Email+password login"}


def test_propose_writes_postgres_before_neptune():
    """Happy path: Postgres version written, then Neptune merge."""
    call_order = []

    def pg_side_effect(**kw):
        call_order.append("postgres")
        return "pg-version-id"

    def neptune_side_effect(obj):
        call_order.append("neptune")
        return {"id": obj.id, "version_id": obj.version_id}

    with patch("nexus.ontology.service.write_version", side_effect=pg_side_effect), \
         patch("nexus.ontology.service.graph.merge_object", side_effect=neptune_side_effect):
        from nexus.ontology import service
        result = service.propose_object(
            object_type="Feature", tenant_id="forge-t",
            properties=FEATURE_PROPS, actor="founder-1", project_id="proj-p",
        )
    assert result["pg_version_id"] == "pg-version-id"
    assert call_order == ["postgres", "neptune"]


def test_propose_postgres_failure_prevents_neptune_write():
    """If Postgres raises, Neptune must NOT be called."""
    with patch("nexus.ontology.service.write_version",
               side_effect=RuntimeError("postgres down")), \
         patch("nexus.ontology.service.graph.merge_object") as mock_neptune:
        from nexus.ontology import service
        with pytest.raises(RuntimeError, match="postgres down"):
            service.propose_object(
                object_type="Feature", tenant_id="forge-t",
                properties=FEATURE_PROPS, actor="f", project_id="p",
            )
    mock_neptune.assert_not_called()


def test_propose_neptune_failure_preserves_postgres_version():
    """Neptune fails after Postgres succeeds — version row is preserved."""
    with patch("nexus.ontology.service.write_version",
               return_value="preserved-version-id") as mock_pg, \
         patch("nexus.ontology.service.graph.merge_object",
               side_effect=RuntimeError("neptune down")):
        from nexus.ontology import service
        with pytest.raises(RuntimeError, match="neptune down"):
            service.propose_object(
                object_type="Feature", tenant_id="forge-t",
                properties=FEATURE_PROPS, actor="f", project_id="p",
            )
    mock_pg.assert_called_once()
