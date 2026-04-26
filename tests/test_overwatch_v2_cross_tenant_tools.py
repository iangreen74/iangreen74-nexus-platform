"""Tests for the four Phase 1 cross-tenant tools.

Each tool: registry registration, parameter validation, mocked happy
path, and error-path behavior. Smoke against a real tenant lives in
the PR description (manual one-off; not a unit test).
"""
from __future__ import annotations

import os
os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from nexus.overwatch_v2.tools.read_tools import (  # noqa: E402
    _tenant_scope,
    read_aria_conversations,
    read_customer_ontology,
    read_customer_pipeline,
    read_customer_tenant_state,
)
from nexus.overwatch_v2.tools.read_tools._registration import (  # noqa: E402
    register_all_read_tools,
)
from nexus.overwatch_v2.tools.registry import list_tools  # noqa: E402


TENANT = "forge-1dba4143ca24ed1f"


# --- Registry -------------------------------------------------------------

def test_registration_yields_nineteen_tools():
    register_all_read_tools()
    names = {(s.get("toolSpec") or {}).get("name") for s in list_tools(include_mutations=False)}
    assert "read_customer_tenant_state" in names
    assert "read_customer_pipeline" in names
    assert "read_customer_ontology" in names
    assert "read_aria_conversations" in names
    assert len([n for n in names if n]) == 19


# --- Validation propagation ----------------------------------------------

@pytest.mark.parametrize("tool", [
    read_customer_tenant_state,
    read_customer_pipeline,
    read_customer_ontology,
    read_aria_conversations,
])
def test_each_tool_rejects_missing_tenant_id(tool):
    with pytest.raises(_tenant_scope.CrossTenantValidationError):
        tool.handler(tenant_id="")


@pytest.mark.parametrize("tool", [
    read_customer_tenant_state,
    read_customer_pipeline,
])
def test_each_tagged_tool_rejects_malformed_tenant_id(tool):
    with pytest.raises(_tenant_scope.CrossTenantValidationError):
        tool.handler(tenant_id="not-a-forge-id")


# --- read_customer_tenant_state -----------------------------------------

def _mock_resource_list(arns_with_tenant):
    fake = MagicMock()
    fake.get_paginator.return_value.paginate.return_value = [{
        "ResourceTagMappingList": [
            {"ResourceARN": arn, "Tags": [{"Key": "TenantId", "Value": tid}]}
            for arn, tid in arns_with_tenant
        ]
    }]
    return fake


def test_read_customer_tenant_state_aggregates_ecs_alb_cfn():
    cluster_arn = f"arn:aws:ecs:us-east-1:418295677815:cluster/forgescaler-{TENANT[:13]}-cluster"
    service_arn = f"arn:aws:ecs:us-east-1:418295677815:service/forgescaler-{TENANT[:13]}-cluster/forgescaler-{TENANT[:13]}"
    tg_arn = f"arn:aws:elasticloadbalancing:us-east-1:418295677815:targetgroup/forgescaler-{TENANT[:13]}-tg/abc123"
    stack_arn = f"arn:aws:cloudformation:us-east-1:418295677815:stack/forgescaler-deploy-{TENANT[:13]}/uuid"

    tag_client = _mock_resource_list([
        (cluster_arn, TENANT), (service_arn, TENANT),
        (tg_arn, TENANT), (stack_arn, TENANT),
    ])
    ecs_client = MagicMock()
    ecs_client.describe_services.return_value = {"services": [{
        "serviceName": f"forgescaler-{TENANT[:13]}",
        "status": "ACTIVE", "desiredCount": 1, "runningCount": 1,
        "pendingCount": 0, "taskDefinition": "td:1",
        "deployments": [{"rolloutState": "COMPLETED"}],
    }]}
    elbv2_client = MagicMock()
    elbv2_client.describe_target_health.return_value = {
        "TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}}]
    }
    cfn_client = MagicMock()
    cfn_client.describe_stack_events.return_value = {"StackEvents": [{
        "Timestamp": "2026-04-25", "LogicalResourceId": "Service",
        "ResourceType": "AWS::ECS::Service", "ResourceStatus": "UPDATE_COMPLETE",
        "ResourceStatusReason": None,
    }]}
    logs_client = MagicMock()
    logs_client.exceptions.ResourceAlreadyExistsException = type("E", (Exception,), {})

    def fake_client(svc, *a, **k):
        return {"resourcegroupstaggingapi": tag_client, "ecs": ecs_client,
                "elbv2": elbv2_client, "cloudformation": cfn_client,
                "logs": logs_client}[svc]

    with patch("nexus.aws_client._client", side_effect=fake_client):
        result = read_customer_tenant_state.handler(tenant_id=TENANT)

    assert result["tenant_id"] == TENANT
    assert len(result["ecs_services"]) == 1
    assert result["ecs_services"][0]["status"] == "ACTIVE"
    assert len(result["alb_targets"]) == 1
    assert result["alb_targets"][0]["healthy_count"] == 1
    assert len(result["cfn_stacks"]) == 1
    logs_client.put_log_events.assert_called_once()


def test_read_customer_tenant_state_raises_on_cross_tenant_leak():
    """If tagging-API returns a resource with a different TenantId tag,
    assertion must fire."""
    cluster_arn = "arn:aws:ecs:us-east-1:418295677815:cluster/wrong-tenant"
    tag_client = _mock_resource_list([(cluster_arn, "forge-other00000000000")])

    def fake_client(svc, *a, **k):
        return tag_client

    with patch("nexus.aws_client._client", side_effect=fake_client):
        with pytest.raises(_tenant_scope.CrossTenantLeakage):
            read_customer_tenant_state.handler(tenant_id=TENANT)


# --- read_customer_pipeline ---------------------------------------------

def test_read_customer_pipeline_aggregates_codebuild_and_cfn():
    project_arn = f"arn:aws:codebuild:us-east-1:418295677815:project/forgescaler-{TENANT[:13]}"
    stack_arn = f"arn:aws:cloudformation:us-east-1:418295677815:stack/forgescaler-deploy-{TENANT[:13]}/uuid"

    tag_client = _mock_resource_list([(project_arn, TENANT), (stack_arn, TENANT)])
    cb_client = MagicMock()
    cb_client.list_builds_for_project.return_value = {"ids": ["build-1"]}
    cb_client.batch_get_builds.return_value = {"builds": [{
        "id": "build-1", "buildNumber": 42, "buildStatus": "SUCCEEDED",
        "startTime": None, "endTime": None,
        "sourceVersion": "main", "resolvedSourceVersion": "abc1234",
    }]}
    cfn_client = MagicMock()
    cfn_client.describe_stack_events.return_value = {"StackEvents": [{
        "Timestamp": "2026-04-25",
        "ResourceType": "AWS::CloudFormation::Stack",
        "ResourceStatus": "UPDATE_COMPLETE",
    }]}
    logs_client = MagicMock()
    logs_client.exceptions.ResourceAlreadyExistsException = type("E", (Exception,), {})

    def fake_client(svc, *a, **k):
        return {"resourcegroupstaggingapi": tag_client, "codebuild": cb_client,
                "cloudformation": cfn_client, "logs": logs_client}[svc]

    with patch("nexus.aws_client._client", side_effect=fake_client):
        result = read_customer_pipeline.handler(tenant_id=TENANT)

    assert len(result["codebuild_projects"]) == 1
    assert result["codebuild_projects"][0]["recent_builds"][0]["status"] == "SUCCEEDED"
    assert len(result["cloudformation_stacks"]) == 1


# --- read_customer_ontology ---------------------------------------------

def test_read_customer_ontology_runs_each_query_with_tenant_param():
    np_client = MagicMock()
    np_client.execute_query.return_value = {"payload": '{"results": [{"x":1}]}'}
    logs_client = MagicMock()
    logs_client.exceptions.ResourceAlreadyExistsException = type("E", (Exception,), {})

    def fake_client(svc, *a, **k):
        return {"neptune-graph": np_client, "logs": logs_client}[svc]

    with patch("nexus.aws_client._client", side_effect=fake_client):
        result = read_customer_ontology.handler(tenant_id=TENANT)

    assert result["tenant_id"] == TENANT
    assert result["graph_id"] == read_customer_ontology.FORGEWING_GRAPH_ID
    # 4 queries, all called with tid parameter
    assert np_client.execute_query.call_count == 4
    for call in np_client.execute_query.call_args_list:
        kwargs = call.kwargs
        assert kwargs["parameters"] == {"tid": TENANT}
        assert kwargs["graphIdentifier"] == "g-1xwjj34141"


# --- read_aria_conversations --------------------------------------------

def test_read_aria_conversations_filters_each_log_group_with_tenant_substring():
    logs_client = MagicMock()
    logs_client.exceptions.ResourceAlreadyExistsException = type("E", (Exception,), {})
    logs_client.exceptions.ResourceNotFoundException = type("RNF", (Exception,), {})
    logs_client.filter_log_events.return_value = {"events": [
        {"timestamp": 1, "logStreamName": "s1", "message": f"hello {TENANT}"}
    ]}

    def fake_client(svc, *a, **k):
        return logs_client

    with patch("nexus.aws_client._client", side_effect=fake_client):
        result = read_aria_conversations.handler(tenant_id=TENANT, lookback_hours=2)

    assert result["tenant_id"] == TENANT
    assert set(result["events_by_log_group"].keys()) == set(read_aria_conversations.LOG_GROUPS)
    # Each call used the tenant_id as the filter pattern
    for call in logs_client.filter_log_events.call_args_list:
        assert call.kwargs["filterPattern"] == f'"{TENANT}"'
    assert result["total_events"] == len(read_aria_conversations.LOG_GROUPS)


def test_read_aria_conversations_caps_lookback_window():
    logs_client = MagicMock()
    logs_client.exceptions.ResourceAlreadyExistsException = type("E", (Exception,), {})
    logs_client.exceptions.ResourceNotFoundException = type("RNF", (Exception,), {})
    logs_client.filter_log_events.return_value = {"events": []}

    def fake_client(svc, *a, **k):
        return logs_client

    with patch("nexus.aws_client._client", side_effect=fake_client):
        result = read_aria_conversations.handler(tenant_id=TENANT, lookback_hours=999)
    assert result["lookback_hours"] == read_aria_conversations.MAX_WINDOW_HOURS
