"""Tests for Track Q ontology expansion + list_aws_resources tool.

Covers:
- 8 new node types accept valid construction; fail on missing required fields
- propose_object via service layer accepts new types
- list_aws_resources dispatch + pagination cap + error mapping
- Backward compat: existing 13 types still work
- Ingestion idempotency via local_store
"""
import os
os.environ.setdefault("NEXUS_MODE", "local")

from datetime import datetime, timezone  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from nexus.overwatch_v2 import ontology  # noqa: E402
from nexus.overwatch_v2.ontology import local_store  # noqa: E402
from nexus.overwatch_v2.tools.read_tools import list_aws_resources  # noqa: E402
from nexus.overwatch_v2.tools.read_tools.exceptions import (  # noqa: E402
    ToolForbidden, ToolNotFound, ToolThrottled, ToolUnknown,
)


def _reset():
    local_store.reset()


def _common(node_type: str) -> dict:
    return {
        "id": "id-1", "object_type": node_type, "version_id": 1,
        "created_at": "2026-04-25T00:00:00+00:00",
        "valid_from": "2026-04-25T00:00:00+00:00",
        "created_by": "test",
    }


# === Schema acceptance =====================================================

class TestNewNodeTypes:
    def test_registry_now_has_21_types(self):
        assert len(ontology.OBJECT_TYPE_REGISTRY) == 21
        assert len(ontology.NodeType.values()) == 21

    def test_service_valid(self):
        s = ontology.Service(**_common("Service"), name="aria-console", kind="ecs_service")
        assert s.kind == "ecs_service"

    def test_service_missing_kind_raises(self):
        with pytest.raises(ontology.V2SchemaValidationError):
            ontology.Service(**_common("Service"), name="x")

    def test_database_valid(self):
        d = ontology.Database(**_common("Database"),
                              name="overwatch-postgres", engine="postgres")
        assert d.engine == "postgres"

    def test_database_missing_name_raises(self):
        with pytest.raises(ontology.V2SchemaValidationError):
            ontology.Database(**_common("Database"))

    def test_datastore_valid(self):
        d = ontology.DataStore(**_common("DataStore"),
                               name="forgewing-eval-corpus", kind="s3")
        assert d.kind == "s3"

    def test_infrastructure_valid(self):
        i = ontology.Infrastructure(**_common("Infrastructure"),
                                    name="aria-platform-vpc", kind="vpc")
        assert i.kind == "vpc"

    def test_runner_valid(self):
        r = ontology.Runner(**_common("Runner"),
                            name="nexus-runner", kind="github_actions_runner")
        assert r.kind == "github_actions_runner"

    def test_workernode_valid(self):
        w = ontology.WorkerNode(**_common("WorkerNode"),
                                name="i-abc", instance_id="i-abc")
        assert w.instance_id == "i-abc"

    def test_workernode_missing_instance_id_raises(self):
        with pytest.raises(ontology.V2SchemaValidationError):
            ontology.WorkerNode(**_common("WorkerNode"), name="x")

    def test_deployment_valid(self):
        d = ontology.Deployment(**_common("Deployment"),
                                service_name="aria-console", name="aria-console-stack-64")
        assert d.service_name == "aria-console"

    def test_deployment_missing_name_raises(self):
        with pytest.raises(ontology.V2SchemaValidationError):
            ontology.Deployment(**_common("Deployment"), service_name="x")

    def test_deployment_target_valid(self):
        dt = ontology.DeploymentTarget(**_common("DeploymentTarget"),
                                       name="aria-console-svc", kind="ecs_service")
        assert dt.kind == "ecs_service"


# === Service-layer integration =============================================

class TestServiceLayerExpansion:
    def test_propose_service_via_service_layer(self):
        _reset()
        r = ontology.propose_object("Service", {"name": "aria-console",
                                                "kind": "ecs_service"})
        assert r["object_id"]
        assert r["version_id"] == 1

    def test_propose_database_via_service_layer(self):
        _reset()
        r = ontology.propose_object("Database",
                                    {"name": "overwatch-postgres",
                                     "engine": "postgres",
                                     "status": "available"})
        rows = ontology.list_objects_by_type("Database")
        assert len(rows) == 1
        assert rows[0]["engine"] == "postgres"

    def test_propose_unknown_type_still_rejected(self):
        _reset()
        with pytest.raises(ontology.V2SchemaValidationError):
            ontology.propose_object("NotAType", {"name": "x"})

    def test_existing_type_still_accepted_after_expansion(self):
        _reset()
        r = ontology.propose_object("EngineeringTask",
                                    {"title": "t", "description": "d"})
        assert r["object_id"]

    def test_list_objects_by_type_filters_correctly(self):
        _reset()
        ontology.propose_object("Service", {"name": "a", "kind": "ecs_cluster"})
        ontology.propose_object("Database", {"name": "b", "engine": "postgres"})
        assert len(ontology.list_objects_by_type("Service")) == 1
        assert len(ontology.list_objects_by_type("Database")) == 1
        assert len(ontology.list_objects_by_type("DataStore")) == 0


# === list_aws_resources tool ==============================================

class _BotoErr(Exception):
    def __init__(self, code: str, msg: str = ""):
        self.response = {"Error": {"Code": code, "Message": msg}}
        super().__init__(f"{code}: {msg}")


def _patch_aws(client_mock):
    return patch("nexus.aws_client._client", return_value=client_mock)


class TestListAwsResources:
    def test_unknown_resource_type_raises(self):
        with pytest.raises(ToolUnknown):
            list_aws_resources.handler(resource_type="not_a_thing")

    def test_ecs_clusters_happy(self):
        m = MagicMock()
        m.get_paginator.return_value.paginate.return_value = [{
            "clusterArns": [
                "arn:aws:ecs:us-east-1:418295677815:cluster/overwatch-platform",
                "arn:aws:ecs:us-east-1:418295677815:cluster/aria-platform",
            ]
        }]
        with _patch_aws(m):
            r = list_aws_resources.handler(resource_type="ecs_clusters")
        assert r["count"] == 2
        assert r["truncated"] is False
        assert any(it["name"] == "overwatch-platform" for it in r["items"])

    def test_ecs_services_uses_filter_cluster(self):
        m = MagicMock()
        m.get_paginator.return_value.paginate.return_value = [{
            "serviceArns": ["arn:aws:ecs:us-east-1:418295677815:service/c/aria-console"],
        }]
        with _patch_aws(m):
            r = list_aws_resources.handler(resource_type="ecs_services",
                                           filters={"cluster": "c"})
        assert r["cluster"] == "c"
        assert r["count"] == 1

    def test_lambda_functions_happy(self):
        m = MagicMock()
        m.get_paginator.return_value.paginate.return_value = [
            {"Functions": [{"FunctionName": "fn-a", "FunctionArn": "arn:a",
                            "Runtime": "python3.11", "LastModified": "2026"}]},
        ]
        with _patch_aws(m):
            r = list_aws_resources.handler(resource_type="lambda_functions")
        assert r["count"] == 1
        assert r["items"][0]["runtime"] == "python3.11"

    def test_rds_instances_extracts_endpoint(self):
        m = MagicMock()
        m.get_paginator.return_value.paginate.return_value = [
            {"DBInstances": [{"DBInstanceIdentifier": "overwatch-postgres",
                              "Engine": "postgres", "DBInstanceStatus": "available",
                              "Endpoint": {"Address": "host.local"},
                              "DBInstanceArn": "arn:rds"}]},
        ]
        with _patch_aws(m):
            r = list_aws_resources.handler(resource_type="rds_instances")
        assert r["count"] == 1
        assert r["items"][0]["endpoint"] == "host.local"

    def test_s3_buckets_happy(self):
        m = MagicMock()
        m.list_buckets.return_value = {"Buckets": [
            {"Name": "forgewing-eval-corpus",
             "CreationDate": datetime.now(timezone.utc)}]}
        with _patch_aws(m):
            r = list_aws_resources.handler(resource_type="s3_buckets")
        assert r["count"] == 1

    def test_step_function_executions_requires_arn(self):
        with pytest.raises(ToolUnknown):
            list_aws_resources.handler(resource_type="step_function_executions")

    def test_step_function_executions_with_arn(self):
        m = MagicMock()
        m.get_paginator.return_value.paginate.return_value = [
            {"executions": [{"executionArn": "arn:e", "name": "e1",
                             "status": "SUCCEEDED",
                             "startDate": datetime.now(timezone.utc)}]},
        ]
        with _patch_aws(m):
            r = list_aws_resources.handler(
                resource_type="step_function_executions",
                filters={"state_machine_arn": "arn:sm"})
        assert r["count"] == 1

    def test_pagination_cap_at_200(self):
        # Producer returns 250 items; we should cap at 200.
        big = [{"FunctionName": f"fn-{i}", "FunctionArn": f"arn:fn-{i}",
                "Runtime": "python3.11", "LastModified": "2026"}
               for i in range(250)]
        m = MagicMock()
        m.get_paginator.return_value.paginate.return_value = [{"Functions": big}]
        with _patch_aws(m):
            r = list_aws_resources.handler(resource_type="lambda_functions")
        assert r["count"] == 200
        assert r["truncated"] is True

    def test_access_denied_raises_forbidden(self):
        m = MagicMock()
        m.get_paginator.return_value.paginate.side_effect = _BotoErr("AccessDenied")
        with _patch_aws(m), pytest.raises(ToolForbidden):
            list_aws_resources.handler(resource_type="ecs_clusters")

    def test_throttling_raises_throttled(self):
        m = MagicMock()
        m.get_paginator.return_value.paginate.side_effect = _BotoErr("ThrottlingException")
        with _patch_aws(m), pytest.raises(ToolThrottled):
            list_aws_resources.handler(resource_type="ecs_clusters")

    def test_not_found_raises_not_found(self):
        m = MagicMock()
        m.get_paginator.return_value.paginate.side_effect = _BotoErr("ResourceNotFoundException")
        with _patch_aws(m), pytest.raises(ToolNotFound):
            list_aws_resources.handler(resource_type="rds_instances")


# === Registration ==========================================================

class TestRegistration:
    def test_register_tool_calls_register(self):
        from unittest.mock import MagicMock
        import sys
        fake = MagicMock()
        fake.RISK_LOW = "low"
        class _ToolSpec:
            def __init__(self, **kw):
                self.__dict__.update(kw)
        fake.ToolSpec = _ToolSpec
        fake.register = MagicMock()
        with patch.dict(sys.modules,
                        {"nexus.overwatch_v2.tools.registry": fake}):
            list_aws_resources.register_tool()
        assert fake.register.call_count == 1
        spec = fake.register.call_args[0][0]
        assert spec.name == "list_aws_resources"
        assert spec.requires_approval is False

    def test_register_all_includes_list_aws_resources(self):
        from unittest.mock import MagicMock
        import sys
        fake = MagicMock()
        fake.RISK_LOW = "low"
        class _ToolSpec:
            def __init__(self, **kw):
                self.__dict__.update(kw)
        fake.ToolSpec = _ToolSpec
        fake.register = MagicMock()
        with patch.dict(sys.modules,
                        {"nexus.overwatch_v2.tools.registry": fake}):
            from nexus.overwatch_v2.tools.read_tools._registration import (
                register_all_read_tools,
            )
            register_all_read_tools()
        names = {c.args[0].name for c in fake.register.call_args_list}
        assert "list_aws_resources" in names
        # Phase 0a (Track C) added 4 codebase-indexing tools.
        # Phase 1 added 4 cross-tenant read tools (count: 15).
        # Phase 0b added 4 cross-source-log tools (count: 19).
        assert len(names) == 19


# === Idempotency for ingestion-style upserts =============================

class TestIngestionIdempotency:
    def test_propose_then_update_no_duplicates(self):
        _reset()
        r1 = ontology.propose_object("Service",
                                     {"name": "aria-console", "kind": "ecs_service"})
        ontology.update_object(r1["object_id"],
                               {"status": "ACTIVE"})
        rows = ontology.list_objects_by_type("Service")
        assert len(rows) == 1, "update should not create a duplicate"
        assert rows[0]["status"] == "ACTIVE"
