"""Tests for Surgeon operator actions."""
import os
from unittest.mock import patch

os.environ.setdefault("NEXUS_MODE", "local")

import pytest
from nexus.operator_actions import create_default_project, OperatorActionResult


def test_create_default_project_new(monkeypatch):
    """Tenant exists, no Project: returns created=True."""
    calls = []

    def _mock_query(q, params=None):
        calls.append(q)
        if "MATCH (t:Tenant" in q:
            return [{"company": "Acme", "repo": "acme/app"}]
        if "MATCH (p:Project" in q:
            return []  # no existing project
        return []  # MERGE + audit writes

    monkeypatch.setattr("nexus.operator_actions._graph_query", _mock_query)
    result = create_default_project("forge-test-123", "ian")
    assert isinstance(result, OperatorActionResult)
    assert result.created is True
    assert result.project_id == "forge-test-123"
    assert result.audit_id.startswith("op-")
    # Should have: tenant check, project check, project merge, audit write
    assert len(calls) >= 4


def test_create_default_project_already_exists(monkeypatch):
    """Tenant exists, Project exists: returns created=False."""
    def _mock_query(q, params=None):
        if "MATCH (t:Tenant" in q:
            return [{"company": "Acme", "repo": "acme/app"}]
        if "MATCH (p:Project" in q:
            return [{"pid": "forge-test-123"}]
        return []

    monkeypatch.setattr("nexus.operator_actions._graph_query", _mock_query)
    result = create_default_project("forge-test-123")
    assert result.created is False
    assert result.audit_id.startswith("op-")


def test_create_default_project_tenant_missing(monkeypatch):
    """Tenant doesn't exist: raises ValueError."""
    monkeypatch.setattr("nexus.operator_actions.MODE", "production")

    def _mock_query(q, params=None):
        if "MATCH (t:Tenant" in q:
            return []
        return []

    monkeypatch.setattr("nexus.operator_actions._graph_query", _mock_query)
    with pytest.raises(ValueError, match="not found"):
        create_default_project("forge-nonexistent")
