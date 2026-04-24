"""Tests for Surgeon #3 — reingest-tenant."""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.operator_reingest import (
    RateLimitError,
    ReingestResult,
    reingest_tenant,
)


def _mock_graph(tenant=True, project=True, recent_ingest=False):
    """Return a side_effect function for _graph_query."""
    def fake(cypher, params=None):
        if "Tenant" in cypher and "RETURN" in cypher:
            if not tenant:
                return []
            return [{"tid": "t1", "repo": "https://github.com/x/y",
                     "active_pid": "proj-1"}]
        if "Project" in cypher and "project_id" in cypher:
            if not project:
                return []
            return [{"pid": "proj-1"}]
        if "IngestRun" in cypher:
            if recent_ingest:
                return [{"started": datetime.now(timezone.utc).isoformat()}]
            return []
        if "MERGE" in cypher or "SET" in cypher:
            return []
        return []
    return fake


@patch("nexus.operator_reingest._graph_query")
@patch("nexus.operator_reingest._record_operator_action", return_value="op-test")
@patch("nexus.operator_reingest.call_api", create=True)
def test_valid_reingest(mock_api, mock_audit, mock_gq):
    mock_gq.side_effect = _mock_graph()
    with patch("nexus.capabilities.forgewing_api.call_api",
               return_value={"status": "reingest_started"}):
        r = reingest_tenant("t1", project_id="proj-1")
    assert isinstance(r, ReingestResult)
    assert r.audit_id == "op-test"
    assert r.project_id == "proj-1"
    assert r.status == "reingest_started"


@patch("nexus.operator_reingest._graph_query")
def test_missing_tenant(mock_gq):
    mock_gq.side_effect = _mock_graph(tenant=False)
    with pytest.raises(ValueError, match="not found"):
        reingest_tenant("t-missing")


@patch("nexus.operator_reingest._graph_query")
def test_missing_project(mock_gq):
    def fake(cypher, params=None):
        if "Tenant" in cypher and "RETURN" in cypher:
            return [{"tid": "t1", "repo": "https://x/y", "active_pid": ""}]
        if "Project" in cypher:
            return []
        return []
    mock_gq.side_effect = fake
    with pytest.raises(ValueError, match="no active project"):
        reingest_tenant("t1")


@patch("nexus.operator_reingest._graph_query")
def test_rate_limit(mock_gq):
    mock_gq.side_effect = _mock_graph(recent_ingest=True)
    with pytest.raises(RateLimitError):
        reingest_tenant("t1", project_id="proj-1")


@patch("nexus.operator_reingest._graph_query")
@patch("nexus.operator_reingest._record_operator_action", return_value="op-f")
def test_force_bypasses_rate_limit(mock_audit, mock_gq):
    mock_gq.side_effect = _mock_graph(recent_ingest=True)
    with patch("nexus.capabilities.forgewing_api.call_api",
               return_value={"status": "reingest_started"}):
        r = reingest_tenant("t1", project_id="proj-1", force=True)
    assert r.status == "reingest_started"


@patch("nexus.operator_reingest._graph_query")
@patch("nexus.operator_reingest._record_operator_action", return_value="op-e")
def test_downstream_failure(mock_audit, mock_gq):
    mock_gq.side_effect = _mock_graph()
    with patch("nexus.capabilities.forgewing_api.call_api",
               side_effect=Exception("timeout")):
        r = reingest_tenant("t1", project_id="proj-1")
    assert "call_failed" in r.status


@patch("nexus.operator_reingest._graph_query")
@patch("nexus.operator_reingest._record_operator_action", return_value="op-r")
def test_resolves_active_project(mock_audit, mock_gq):
    def fake(cypher, params=None):
        if "Tenant" in cypher and "RETURN" in cypher:
            return [{"tid": "t1", "repo": "https://x/y", "active_pid": ""}]
        if "Project" in cypher and "status: 'active'" in cypher:
            return [{"pid": "proj-auto"}]
        if "Project" in cypher and "project_id" in cypher:
            return [{"pid": "proj-auto"}]
        return []
    mock_gq.side_effect = fake
    with patch("nexus.capabilities.forgewing_api.call_api",
               return_value={"status": "ok"}):
        r = reingest_tenant("t1")
    assert r.project_id == "proj-auto"
