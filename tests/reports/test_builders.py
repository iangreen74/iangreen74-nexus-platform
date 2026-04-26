"""Per-builder unit tests with mocked tool_ctx / boto."""
from __future__ import annotations

import os
os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from nexus.reports.builders import (  # noqa: E402
    fleet_health, pipeline_activity, tenant_profile,
)
from nexus.reports.tool_ctx import ToolCtx  # noqa: E402


# --- fleet_health ----------------------------------------------------------

def _mock_tag_client(tenant_ids: list[str]) -> MagicMock:
    fake = MagicMock()
    fake.get_paginator.return_value.paginate.return_value = [{
        "ResourceTagMappingList": [
            {"ResourceARN": f"arn:aws:ecs:us-east-1:x:cluster/{tid}",
             "Tags": [{"Key": "TenantId", "Value": tid}]}
            for tid in tenant_ids
        ],
    }]
    return fake


def test_fleet_health_classifies_green_amber_red():
    states = {
        "forge-aaa": {  # green
            "ecs_services": [{"desired": 1, "running": 1}],
            "alb_targets": [{"healthy_count": 1, "unhealthy_count": 0}],
        },
        "forge-bbb": {  # amber: partial running
            "ecs_services": [{"desired": 2, "running": 1}],
            "alb_targets": [{"healthy_count": 1, "unhealthy_count": 0}],
        },
        "forge-ccc": {  # red: 0 healthy targets, some unhealthy
            "ecs_services": [{"desired": 1, "running": 1}],
            "alb_targets": [{"healthy_count": 0, "unhealthy_count": 1}],
        },
    }

    def fake_state(**kw):
        return states[kw["tenant_id"]]

    ctx = ToolCtx(handlers={"read_customer_tenant_state": fake_state})
    with patch("nexus.aws_client._client",
               return_value=_mock_tag_client(list(states))):
        sections = fleet_health.build({}, ctx)

    totals = next(s for s in sections if s["title"] == "Fleet totals")["data"]
    assert totals["total"] == 3
    assert totals["green"] == 1
    assert totals["amber"] == 1
    assert totals["red"] == 1


def test_fleet_health_buckets_tenant_with_zero_running_as_red():
    state = {
        "ecs_services": [{"desired": 1, "running": 0}],
        "alb_targets": [],
    }
    ctx = ToolCtx(handlers={
        "read_customer_tenant_state": lambda **kw: state,
    })
    with patch("nexus.aws_client._client",
               return_value=_mock_tag_client(["forge-x"])):
        sections = fleet_health.build({}, ctx)
    totals = next(s for s in sections if s["title"] == "Fleet totals")["data"]
    assert totals["red"] == 1


def test_fleet_health_handles_per_tenant_error():
    def boom(**kw):
        raise RuntimeError("tenant unreachable")
    ctx = ToolCtx(handlers={"read_customer_tenant_state": boom})
    with patch("nexus.aws_client._client",
               return_value=_mock_tag_client(["forge-x"])):
        sections = fleet_health.build({}, ctx)
    totals = next(s for s in sections if s["title"] == "Fleet totals")["data"]
    assert totals["red"] == 1


def test_fleet_health_surfaces_trend_deferred_text():
    ctx = ToolCtx(handlers={
        "read_customer_tenant_state": lambda **kw: {
            "ecs_services": [], "alb_targets": [],
        },
    })
    with patch("nexus.aws_client._client",
               return_value=_mock_tag_client([])):
        sections = fleet_health.build({}, ctx)
    trend = next(s for s in sections if "trend" in s["title"].lower())
    assert "deferred" in trend["data"]["text"].lower()


# --- pipeline_activity -----------------------------------------------------

def test_pipeline_activity_aggregates_by_status_within_window():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    in_window = (now - timedelta(hours=1)).isoformat()
    out_of_window = (now - timedelta(hours=72)).isoformat()

    pipelines = {
        "forge-aaa": {
            "codebuild_projects": [{
                "project_name": "forgescaler-forge-aaa",
                "recent_builds": [
                    {"id": "b1", "status": "SUCCEEDED", "started": in_window,
                     "duration_seconds": 120, "source_version": "main"},
                    {"id": "b2", "status": "FAILED", "started": in_window,
                     "duration_seconds": 60, "source_version": "main"},
                    # Out-of-window build: ignored.
                    {"id": "b3", "status": "SUCCEEDED", "started": out_of_window,
                     "duration_seconds": 90, "source_version": "main"},
                ],
            }],
            "cloudformation_stacks": [],
        },
    }
    ctx = ToolCtx(handlers={
        "read_customer_pipeline": lambda **kw: pipelines[kw["tenant_id"]],
    })
    with patch("nexus.aws_client._client",
               return_value=_mock_tag_client(list(pipelines))):
        sections = pipeline_activity.build({}, ctx)

    totals = next(s for s in sections if "totals" in s["title"].lower())["data"]
    assert totals["total_builds"] == 2  # in-window only
    assert totals["by_status"]["SUCCEEDED"] == 1
    assert totals["by_status"]["FAILED"] == 1
    assert totals["success_rate"] == 0.5

    failed = next(s for s in sections if "failed" in s["title"].lower())["data"]
    assert any(r["build_id"] == "b2" for r in failed["rows"])


def test_pipeline_activity_handles_per_tenant_error():
    def boom(**kw):
        raise RuntimeError("nope")
    ctx = ToolCtx(handlers={"read_customer_pipeline": boom})
    with patch("nexus.aws_client._client",
               return_value=_mock_tag_client(["forge-x"])):
        sections = pipeline_activity.build({}, ctx)
    per_tenant = next(s for s in sections if "per-tenant" in s["title"].lower())["data"]
    rows = per_tenant["rows"]
    assert len(rows) == 1
    assert rows[0]["builds_in_window"] == 0
    assert "error" in rows[0]


# --- tenant_profile --------------------------------------------------------

def test_tenant_profile_requires_tenant_id():
    ctx = ToolCtx(handlers={})
    with pytest.raises(ValueError):
        tenant_profile.build({}, ctx)
    with pytest.raises(ValueError):
        tenant_profile.build({"tenant_id": ""}, ctx)
    with pytest.raises(ValueError):
        tenant_profile.build({"tenant_id": "not-a-forge-id"}, ctx)


def test_tenant_profile_renders_all_sections_when_tools_succeed():
    ctx = ToolCtx(handlers={
        "read_customer_tenant_state": lambda **kw: {
            "ecs_services": [{"name": "svc-1", "status": "ACTIVE",
                              "desired": 1, "running": 1, "rollout_state": "COMPLETED"}],
            "alb_targets": [{"tg_name": "tg-1", "healthy_count": 1,
                             "unhealthy_count": 0, "states": ["healthy"]}],
            "cfn_stacks": [{"stack_name": "stack-1", "recent_events": []}],
            "captured_at": "2026-04-26T00:00:00+00:00",
        },
        "read_customer_pipeline": lambda **kw: {
            "codebuild_projects": [{
                "project_name": "p1",
                "recent_builds": [{"id": "b1", "status": "SUCCEEDED",
                                   "started": None, "duration_seconds": None,
                                   "source_version": None}],
            }],
        },
        "read_aria_conversations": lambda **kw: {
            "total_events": 5,
            "events_by_log_group": {"/aria/console": [], "/aria/daemon": []},
        },
        "read_customer_ontology": lambda **kw: {
            "counts": {"tenant_node": 1, "recent_tasks": 3},
        },
    })
    sections = tenant_profile.build({"tenant_id": "forge-1dba4143ca24ed1f"}, ctx)
    titles = [s["title"] for s in sections]
    assert "Identity" in titles
    assert "ECS services" in titles
    assert "ALB target health" in titles
    assert "Recent deploys" in titles
    assert "ARIA conversation activity" in titles
    assert "Ontology object counts" in titles


def test_tenant_profile_swallows_per_tool_failure_and_renders_text_fallback():
    """When a single tool errors, the corresponding section becomes a
    text 'unavailable' note — the rest of the report still renders."""
    ctx = ToolCtx(handlers={
        "read_customer_tenant_state": lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        "read_customer_pipeline": lambda **kw: {"codebuild_projects": []},
        "read_aria_conversations": lambda **kw: {"total_events": 0,
                                                  "events_by_log_group": {}},
        "read_customer_ontology": lambda **kw: {"counts": {}},
    })
    sections = tenant_profile.build({"tenant_id": "forge-1dba4143ca24ed1f"}, ctx)
    ecs_section = next(s for s in sections
                       if s["title"] in ("ECS / ALB / CFN", "ECS services"))
    assert ecs_section["kind"] == "text"
    assert "unavailable" in ecs_section["data"]["text"].lower()
