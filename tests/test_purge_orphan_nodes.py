"""Tests for Surgeon #4: purge-orphan-nodes."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest
from nexus.operator_purge import purge_orphan_nodes


def test_dry_run_with_orphans(monkeypatch):
    """Dry-run returns counts without mutating."""
    calls = []

    def _mock(q, params=None):
        calls.append(q)
        if "MATCH (t:Tenant" in q:
            return [{"tid": "forge-test"}]
        if "project_id IS NULL" in q and "MissionBrief" in q:
            return [{"c": 3}]
        if "project_id = $tid" in q and "MissionBrief" in q:
            return [{"c": 1}]
        if "project_id IS NULL" in q and "OmniscientInsight" in q:
            return [{"c": 14}]
        return [{"c": 0}]

    monkeypatch.setattr("nexus.operator_purge._graph_query", _mock)
    result = purge_orphan_nodes(
        "forge-test",
        labels_to_purge=["MissionBrief", "OmniscientInsight"],
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["total_affected"] == 4 + 14
    assert result["per_label"]["MissionBrief"]["would_purge"] == 4
    assert "null_pid" in result["per_label"]["MissionBrief"]["from_patterns"]
    assert "tid_fallback" in result["per_label"]["MissionBrief"]["from_patterns"]
    assert result["per_label"]["OmniscientInsight"]["would_purge"] == 14
    assert result["audit_id"].startswith("op-")
    # No DETACH DELETE in dry-run.
    assert not any("DETACH DELETE" in c for c in calls)


def test_execute_deletes_and_audits(monkeypatch):
    """dry_run=False runs DETACH DELETE per label and records audit."""
    calls = []

    def _mock(q, params=None):
        calls.append(q)
        if "MATCH (t:Tenant" in q:
            return [{"tid": "forge-test"}]
        if "project_id IS NULL" in q:
            return [{"c": 2}]
        if "project_id = $tid" in q and "RETURN count" in q:
            return [{"c": 1}]
        if "DETACH DELETE" in q:
            return [{"deleted": 3}]
        if "OperatorAction" in q:
            return []
        return [{"c": 0}]

    monkeypatch.setattr("nexus.operator_purge._graph_query", _mock)
    result = purge_orphan_nodes(
        "forge-test",
        labels_to_purge=["MissionBrief"],
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert any("DETACH DELETE" in c for c in calls)
    # Every DETACH DELETE must be label-scoped.
    for c in calls:
        if "DETACH DELETE" in c:
            assert "MATCH (n:MissionBrief" in c


def test_non_project_scoped_label_dropped(monkeypatch):
    """OverwatchTenantSnapshot etc. are silently ignored, never purged."""
    def _mock(q, params=None):
        if "MATCH (t:Tenant" in q:
            return [{"tid": "forge-test"}]
        return [{"c": 0}]

    monkeypatch.setattr("nexus.operator_purge._graph_query", _mock)
    result = purge_orphan_nodes(
        "forge-test",
        labels_to_purge=["OverwatchTenantSnapshot", "MissionBrief"],
        dry_run=True,
    )
    assert "OverwatchTenantSnapshot" in result["ignored_labels"]
    assert "MissionBrief" in result["per_label"]
    assert "OverwatchTenantSnapshot" not in result["per_label"]


def test_empty_labels_raises(monkeypatch):
    monkeypatch.setattr("nexus.operator_purge._graph_query",
                        lambda q, p=None: [{"tid": "forge-test"}])
    with pytest.raises(ValueError, match="required"):
        purge_orphan_nodes("forge-test", labels_to_purge=[])


def test_none_labels_raises():
    with pytest.raises(ValueError, match="required"):
        purge_orphan_nodes("forge-test", labels_to_purge=None)  # type: ignore[arg-type]


def test_tenant_missing_raises(monkeypatch):
    monkeypatch.setattr("nexus.operator_purge.MODE", "production")
    monkeypatch.setattr("nexus.operator_purge._graph_query",
                        lambda q, p=None: [])
    with pytest.raises(ValueError, match="not found"):
        purge_orphan_nodes(
            "forge-nope", labels_to_purge=["MissionBrief"],
        )


def test_null_pid_and_tid_fallback_both_counted(monkeypatch):
    """Both orphan patterns contribute to the per-label total."""
    def _mock(q, params=None):
        if "MATCH (t:Tenant" in q:
            return [{"tid": "forge-test"}]
        if "project_id IS NULL" in q:
            return [{"c": 7}]
        if "project_id = $tid" in q and "RETURN count" in q:
            return [{"c": 3}]
        return [{"c": 0}]

    monkeypatch.setattr("nexus.operator_purge._graph_query", _mock)
    result = purge_orphan_nodes(
        "forge-test",
        labels_to_purge=["MissionBrief"],
        dry_run=True,
    )
    patterns = result["per_label"]["MissionBrief"]["from_patterns"]
    assert "null_pid" in patterns
    assert "tid_fallback" in patterns
    assert result["per_label"]["MissionBrief"]["would_purge"] == 10


def test_no_orphans_idempotent(monkeypatch):
    def _mock(q, params=None):
        if "MATCH (t:Tenant" in q:
            return [{"tid": "forge-test"}]
        return [{"c": 0}]

    monkeypatch.setattr("nexus.operator_purge._graph_query", _mock)
    result = purge_orphan_nodes(
        "forge-test",
        labels_to_purge=["MissionBrief"],
        dry_run=True,
    )
    assert result["total_affected"] == 0
