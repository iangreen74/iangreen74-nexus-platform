"""Tests for Surgeon #2: repair-orphan-nodes."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest
from nexus.operator_actions import repair_orphan_nodes, PROJECT_SCOPED_LABELS


def test_dry_run_with_orphans(monkeypatch):
    """Dry-run returns counts without mutating."""
    def _mock(q, params=None):
        if "MATCH (t:Tenant" in q:
            return [{"tid": "forge-test"}]
        if "MATCH (p:Project" in q:
            return [{"pid": "proj-123"}]
        if "project_id IS NULL" in q and "MissionBrief" in q:
            return [{"c": 3}]
        if "project_id = $tid" in q and "MissionBrief" in q:
            return [{"c": 1}]
        return [{"c": 0}]

    monkeypatch.setattr("nexus.operator_repair._graph_query", _mock)
    result = repair_orphan_nodes("forge-test", "proj-123", dry_run=True)
    assert result["dry_run"] is True
    assert result["total_affected"] >= 4
    assert result["per_label"]["MissionBrief"]["would_relabel"] == 4
    assert "null_pid" in result["per_label"]["MissionBrief"]["from_patterns"]
    assert "tid_fallback" in result["per_label"]["MissionBrief"]["from_patterns"]
    assert result["audit_id"].startswith("op-")


def test_execute_relabels(monkeypatch):
    """dry_run=False executes SET queries and records audit."""
    calls = []

    def _mock(q, params=None):
        calls.append(q)
        if "MATCH (t:Tenant" in q:
            return [{"tid": "forge-test"}]
        if "MATCH (p:Project" in q:
            return [{"pid": "proj-123"}]
        if "project_id IS NULL" in q:
            return [{"c": 2}]
        if "project_id = $tid" in q and "RETURN count" in q:
            return [{"c": 1}]
        if "SET n.project_id" in q:
            return [{"updated": 3}]
        return [{"c": 0}]

    monkeypatch.setattr("nexus.operator_repair._graph_query", _mock)
    result = repair_orphan_nodes(
        "forge-test", "proj-123",
        labels_to_repair=["MissionBrief"],
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert any("SET n.project_id" in c for c in calls)


def test_tenant_missing_raises(monkeypatch):
    monkeypatch.setattr("nexus.operator_repair.MODE", "production")
    monkeypatch.setattr("nexus.operator_repair._graph_query",
                        lambda q, p=None: [])
    with pytest.raises(ValueError, match="not found"):
        repair_orphan_nodes("forge-nope", "proj-123")


def test_project_missing_raises(monkeypatch):
    monkeypatch.setattr("nexus.operator_repair.MODE", "production")

    def _mock(q, params=None):
        if "MATCH (t:Tenant" in q:
            return [{"tid": "forge-test"}]
        return []  # no project

    monkeypatch.setattr("nexus.operator_repair._graph_query", _mock)
    with pytest.raises(ValueError, match="Project.*not found"):
        repair_orphan_nodes("forge-test", "proj-nope")


def test_invalid_label_dropped(monkeypatch):
    def _mock(q, params=None):
        if "Tenant" in q or "Project" in q:
            return [{"tid": "t", "pid": "p"}]
        return [{"c": 0}]

    monkeypatch.setattr("nexus.operator_repair._graph_query", _mock)
    result = repair_orphan_nodes(
        "forge-test", "proj-123",
        labels_to_repair=["OverwatchTenantSnapshot", "MissionBrief"],
        dry_run=True,
    )
    assert "OverwatchTenantSnapshot" in result["ignored_labels"]
    assert "MissionBrief" in result["per_label"]
    assert "OverwatchTenantSnapshot" not in result["per_label"]


def test_idempotent_no_orphans(monkeypatch):
    def _mock(q, params=None):
        if "Tenant" in q or "Project" in q:
            return [{"tid": "t", "pid": "p"}]
        return [{"c": 0}]

    monkeypatch.setattr("nexus.operator_repair._graph_query", _mock)
    result = repair_orphan_nodes("forge-test", "proj-123", dry_run=True)
    assert result["total_affected"] == 0
