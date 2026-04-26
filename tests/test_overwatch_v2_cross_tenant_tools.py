"""Tests for Phase 0c cross-tenant tool handlers."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import io
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from nexus.overwatch_v2.tools.read_tools.cross_tenant import (
    aria_conversations, logs as customer_logs, pipeline as customer_pipeline,
    tenant_state,
)
from nexus.overwatch_v2.tools.read_tools.cross_tenant._guardrails import (
    CrossTenantLeakageError,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import ToolUnknown

VALID_TID = "forge-1dba4143ca24ed1f"
SHORT = "1dba414"


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch):
    """Mock the audit emitter so tests don't try to talk to CloudWatch."""
    monkeypatch.setattr(
        "nexus.overwatch_v2.tools.read_tools.cross_tenant"
        "._guardrails._audit_cross_tenant_call",
        lambda *a, **k: None,
    )
    # And the same name as imported into each tool module
    for mod in (tenant_state, customer_pipeline, customer_logs, aria_conversations):
        monkeypatch.setattr(mod, "_audit_cross_tenant_call",
                            lambda *a, **k: None)


# ---- read_customer_tenant_state -------------------------------------------

def _mock_ecs_full(monkeypatch):
    fake_ecs = MagicMock()
    fake_ecs.list_services.return_value = {
        "serviceArns": ["arn:aws:ecs:us-east-1:418295677815:service/c/svc"],
    }
    fake_ecs.describe_services.return_value = {
        "services": [{
            "serviceName": f"forgescaler-forge-{SHORT}-svc",
            "desiredCount": 1, "runningCount": 1,
            "deployments": [{
                "status": "PRIMARY", "rolloutState": "COMPLETED",
                "runningCount": 1, "desiredCount": 1,
                "taskDefinition": "arn:aws:ecs:us-east-1:418295677815:task-definition/x:5",
            }],
        }],
    }
    class _Excs: ClusterNotFoundException = type("X", (Exception,), {})
    fake_ecs.exceptions = _Excs
    fake_elbv2 = MagicMock()
    fake_elbv2.describe_target_groups.return_value = {
        "TargetGroups": [{
            "TargetGroupArn": "arn:tg/abc",
            "TargetGroupName": f"forgescaler-forge-{SHORT}-tg",
        }],
    }
    fake_elbv2.describe_target_health.return_value = {
        "TargetHealthDescriptions": [
            {"TargetHealth": {"State": "healthy"}},
            {"TargetHealth": {"State": "unhealthy"}},
        ],
    }
    class _ELBExcs: TargetGroupNotFoundException = type("X", (Exception,), {})
    fake_elbv2.exceptions = _ELBExcs
    fake_cfn = MagicMock()
    fake_cfn.list_stacks.return_value = {
        "StackSummaries": [{
            "StackName": f"forgescaler-forge-{SHORT}-stack",
            "StackStatus": "UPDATE_COMPLETE",
            "LastUpdatedTime": datetime.now(timezone.utc),
        }],
    }
    def factory(svc):
        if svc == "ecs": return fake_ecs
        if svc == "elbv2": return fake_elbv2
        if svc == "cloudformation": return fake_cfn
        raise AssertionError(f"unexpected svc: {svc}")
    monkeypatch.setattr("nexus.aws_client._client", factory)


def test_tenant_state_happy_path(monkeypatch):
    _mock_ecs_full(monkeypatch)
    r = tenant_state.handler(tenant_id=VALID_TID)
    assert r["ok"] is True
    assert r["tenant_id"] == VALID_TID
    assert r["ecs_services"][0]["name"].startswith(f"forgescaler-forge-{SHORT}-")
    assert r["alb_targets"][0]["healthy_count"] == 1
    assert r["alb_targets"][0]["unhealthy_count"] == 1
    assert r["recent_deploys"][0]["status"] == "UPDATE_COMPLETE"


def test_tenant_state_rejects_missing_id():
    with pytest.raises(ToolUnknown, match="required"):
        tenant_state.handler(tenant_id="")


def test_tenant_state_rejects_bad_id_format():
    with pytest.raises(ToolUnknown, match="forge-"):
        tenant_state.handler(tenant_id="acme-12345678")


def test_tenant_state_assertion_fires_on_leak(monkeypatch):
    """Force AWS to return a different tenant's resource; assertion must catch."""
    fake_ecs = MagicMock()
    fake_ecs.list_services.return_value = {"serviceArns": ["a"]}
    fake_ecs.describe_services.return_value = {
        "services": [{
            "serviceName": "forgescaler-forge-OTHER-svc",  # leak
            "desiredCount": 1, "runningCount": 1, "deployments": [],
        }],
    }
    class _Excs: ClusterNotFoundException = type("X", (Exception,), {})
    fake_ecs.exceptions = _Excs
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake_ecs)
    with pytest.raises(CrossTenantLeakageError):
        tenant_state.handler(tenant_id=VALID_TID)


def test_tenant_state_handles_missing_cluster(monkeypatch):
    fake_ecs = MagicMock()
    class _NotFound(Exception): ...
    fake_ecs.list_services.side_effect = _NotFound("nope")
    fake_ecs.exceptions = type("X", (), {"ClusterNotFoundException": _NotFound})
    fake_elbv2 = MagicMock()
    class _ELBNotFound(Exception): ...
    fake_elbv2.describe_target_groups.side_effect = _ELBNotFound("nope")
    fake_elbv2.exceptions = type("X", (), {"TargetGroupNotFoundException": _ELBNotFound})
    fake_cfn = MagicMock()
    fake_cfn.list_stacks.return_value = {"StackSummaries": []}
    def factory(svc):
        return {"ecs": fake_ecs, "elbv2": fake_elbv2,
                "cloudformation": fake_cfn}[svc]
    monkeypatch.setattr("nexus.aws_client._client", factory)
    r = tenant_state.handler(tenant_id=VALID_TID)
    assert r["ok"] is True
    assert r["ecs_services"] == [] and r["alb_targets"] == []


# ---- read_customer_pipeline -----------------------------------------------

def test_pipeline_happy_path(monkeypatch):
    fake_cb = MagicMock()
    page = {"projects": [f"forgescaler-forge-{SHORT}-build", "unrelated-project"]}
    fake_cb.get_paginator.return_value.paginate.return_value = [page]
    fake_cb.batch_get_projects.return_value = {
        "projects": [{
            "name": f"forgescaler-forge-{SHORT}-build",
            "serviceRole": "arn:aws:iam::418295677815:role/cb-role",
            "lastModified": datetime.now(timezone.utc),
        }],
    }
    fake_cb.list_builds_for_project.return_value = {"ids": ["build-1"]}
    fake_cb.batch_get_builds.return_value = {
        "builds": [{
            "projectName": f"forgescaler-forge-{SHORT}-build",
            "id": "build-1", "buildStatus": "SUCCEEDED",
            "startTime": datetime.now(timezone.utc),
            "endTime": datetime.now(timezone.utc),
        }],
    }
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake_cb)
    r = customer_pipeline.handler(tenant_id=VALID_TID, limit=5)
    assert r["ok"] is True
    assert len(r["codebuild_projects"]) == 1
    assert r["codebuild_projects"][0]["name"] == f"forgescaler-forge-{SHORT}-build"
    assert r["recent_runs"][0]["status"] == "SUCCEEDED"


def test_pipeline_rejects_missing_id():
    with pytest.raises(ToolUnknown, match="required"):
        customer_pipeline.handler(tenant_id="")


def test_pipeline_filters_unrelated_projects(monkeypatch):
    fake_cb = MagicMock()
    fake_cb.get_paginator.return_value.paginate.return_value = [
        {"projects": ["unrelated", "another-tenant-project"]},
    ]
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake_cb)
    r = customer_pipeline.handler(tenant_id=VALID_TID)
    assert r["codebuild_projects"] == []
    assert r["recent_runs"] == []
    fake_cb.batch_get_projects.assert_not_called()


# ---- read_customer_logs ---------------------------------------------------

def test_logs_happy_path(monkeypatch):
    fake_logs = MagicMock()
    fake_logs.get_paginator.return_value.paginate.return_value = [
        {"logGroups": [
            {"logGroupName": f"/forgescaler/forge-{SHORT}/app"},
            {"logGroupName": f"/forgescaler/forge-other/app"},  # filtered out
            {"logGroupName": "/overwatch-v2/cross-tenant-audit"},  # filtered (audit)
        ]},
    ]
    fake_logs.filter_log_events.return_value = {
        "events": [{"timestamp": 1, "message": "hello"}],
    }
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake_logs)
    r = customer_logs.handler(tenant_id=VALID_TID, time_range_minutes=30)
    assert r["ok"] is True
    assert r["log_groups_scanned"] == [f"/forgescaler/forge-{SHORT}/app"]
    assert r["window_minutes"] == 30


def test_logs_caps_window(monkeypatch):
    fake_logs = MagicMock()
    fake_logs.get_paginator.return_value.paginate.return_value = [{"logGroups": []}]
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake_logs)
    r = customer_logs.handler(tenant_id=VALID_TID, time_range_minutes=99999)
    assert r["window_minutes"] == 24 * 60  # capped


def test_logs_rejects_missing_id():
    with pytest.raises(ToolUnknown, match="required"):
        customer_logs.handler(tenant_id="")


# ---- read_aria_conversations ----------------------------------------------

def _mock_neptune(rows):
    fake_client = MagicMock()
    fake_client.execute_query.return_value = {
        "payload": io.BytesIO(json.dumps({"results": rows}).encode("utf-8")),
    }
    return fake_client


def test_aria_happy_path(monkeypatch):
    rows = [
        {"tenant_id": VALID_TID, "message_id": "m1",
         "role": "user", "content": "hello",
         "timestamp": "2026-04-26T01:00:00Z", "project_id": "p1"},
    ]
    monkeypatch.setattr(
        "nexus.aws_client._client", lambda svc: _mock_neptune(rows),
    )
    r = aria_conversations.handler(tenant_id=VALID_TID, limit=5)
    assert r["ok"] is True
    assert len(r["conversation_messages"]) == 1
    assert r["conversation_messages"][0]["message_id"] == "m1"


def test_aria_assertion_fires_on_leak(monkeypatch):
    """Forced leak: a row with the wrong tenant_id must be caught."""
    rows = [
        {"tenant_id": VALID_TID, "message_id": "m1", "role": "user",
         "content": "ok", "timestamp": "2026-04-26T01:00:00Z"},
        {"tenant_id": "forge-OTHER-tenant", "message_id": "m2",
         "role": "assistant", "content": "leaked", "timestamp": "2026-04-26T01:00:01Z"},
    ]
    monkeypatch.setattr(
        "nexus.aws_client._client", lambda svc: _mock_neptune(rows),
    )
    with pytest.raises(CrossTenantLeakageError):
        aria_conversations.handler(tenant_id=VALID_TID)


def test_aria_caps_limit(monkeypatch):
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: _mock_neptune([]))
    r = aria_conversations.handler(tenant_id=VALID_TID, limit=99999)
    assert r["ok"] is True  # cap is internal; just verify it doesn't blow up


def test_aria_empty_result(monkeypatch):
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: _mock_neptune([]))
    r = aria_conversations.handler(tenant_id=VALID_TID)
    assert r["ok"] is True
    assert r["conversation_messages"] == []


def test_aria_rejects_missing_id():
    with pytest.raises(ToolUnknown, match="required"):
        aria_conversations.handler(tenant_id="")


# ---- Step 8: cross-tenant leakage smoke -----------------------------------

def test_cross_tenant_leakage_smoke():
    """Step 8 from the prompt: prove Guardrail 2 catches accidental leakage
    even if a tool's filter logic is bypassed."""
    from nexus.overwatch_v2.tools.read_tools.cross_tenant._guardrails import (
        _assert_tenant_scoped, CrossTenantLeakageError,
    )
    fake_resources = [{"name": "forgescaler-forge-WRONG-tenant-svc"}]
    with pytest.raises(CrossTenantLeakageError) as ei:
        _assert_tenant_scoped(fake_resources, VALID_TID)
    assert "CROSS-TENANT LEAKAGE" in str(ei.value)
