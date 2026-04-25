"""Tests for the six V2 read tools. NEXUS_MODE=local.

Tool modules lazy-import the registry and Track E's ontology, so this test
file is runnable even on a branch where those haven't merged. Tests that
require those external surfaces use ``patch.dict(sys.modules, ...)`` to
inject mocks rather than skipping.
"""
import os
os.environ.setdefault("NEXUS_MODE", "local")

import sys  # noqa: E402
from datetime import datetime, timezone, timedelta  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from nexus.overwatch_v2.tools.read_tools import (  # noqa: E402
    aws_resource, cloudwatch_logs, engineering_ontology, github,
    overwatch_metrics, pipeline_truth,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import (  # noqa: E402
    ToolForbidden, ToolNotFound, ToolThrottled, ToolUnknown, map_boto_error,
)


# --- Helpers ----------------------------------------------------------------

class _BotoClientErrorLike(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        self.response = {"Error": {"Code": code, "Message": msg}}
        super().__init__(f"{code}: {msg}")


def _patch_aws(mock_client):
    return patch("nexus.aws_client._client", return_value=mock_client)


# === TestAwsResource =======================================================

class TestAwsResource:
    def test_cfn_stack_happy(self):
        m = MagicMock()
        m.describe_stacks.return_value = {"Stacks": [{
            "StackStatus": "CREATE_COMPLETE",
            "CreationTime": datetime(2026, 4, 24, tzinfo=timezone.utc),
            "Outputs": [{"OutputKey": "K", "OutputValue": "V"}],
            "Parameters": [{"ParameterKey": "P", "ParameterValue": "X"}],
        }]}
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="cfn_stack", identifier="ex")
        assert r["status"] == "CREATE_COMPLETE"
        assert r["outputs"] == {"K": "V"}
        assert r["parameters"] == {"P": "X"}

    def test_cfn_stack_not_found_raises(self):
        m = MagicMock()
        m.describe_stacks.side_effect = _BotoClientErrorLike("ValidationError", "stack does not exist")
        with _patch_aws(m), pytest.raises(ToolNotFound):
            aws_resource.handler(resource_type="cfn_stack", identifier="ghost")

    def test_cfn_stack_forbidden_raises(self):
        m = MagicMock()
        m.describe_stacks.side_effect = _BotoClientErrorLike("AccessDenied", "no")
        with _patch_aws(m), pytest.raises(ToolForbidden):
            aws_resource.handler(resource_type="cfn_stack", identifier="x")

    def test_cfn_stack_throttled_raises(self):
        m = MagicMock()
        m.describe_stacks.side_effect = _BotoClientErrorLike("ThrottlingException")
        with _patch_aws(m), pytest.raises(ToolThrottled):
            aws_resource.handler(resource_type="cfn_stack", identifier="x")

    def test_ecs_task_happy(self):
        m = MagicMock()
        m.describe_tasks.return_value = {"tasks": [{
            "lastStatus": "RUNNING", "desiredStatus": "RUNNING",
            "stoppedReason": None, "taskDefinitionArn": "arn:td:1",
        }]}
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="ecs_task",
                                     identifier="arn:t", cluster="c")
        assert r["last_status"] == "RUNNING"

    def test_ecs_service_happy(self):
        m = MagicMock()
        m.describe_services.return_value = {"services": [{
            "status": "ACTIVE", "desiredCount": 1, "runningCount": 1,
            "taskDefinition": "arn:td:2",
        }]}
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="ecs_service",
                                     identifier="svc", cluster="c")
        assert r["status"] == "ACTIVE"
        assert r["running_count"] == 1

    def test_iam_role_happy(self):
        m = MagicMock()
        m.get_role.return_value = {"Role": {
            "Arn": "arn:iam:role/x", "CreateDate": datetime.now(timezone.utc),
            "AssumeRolePolicyDocument": {"Version": "2012"}, "Description": "d",
        }}
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="iam_role", identifier="x")
        assert r["arn"].endswith("/x")

    def test_lambda_happy(self):
        m = MagicMock()
        m.get_function.return_value = {"Configuration": {
            "FunctionArn": "arn:lambda:fn", "Runtime": "python3.11",
            "LastModified": "2026-04-01", "State": "Active",
        }}
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="lambda_function", identifier="fn")
        assert r["runtime"] == "python3.11"

    def test_sfn_execution_happy(self):
        m = MagicMock()
        m.describe_execution.return_value = {
            "status": "SUCCEEDED",
            "startDate": datetime.now(timezone.utc),
            "stopDate": datetime.now(timezone.utc),
            "output": '{"k":"v"}',
        }
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="sfn_execution",
                                     identifier="arn:exec")
        assert r["status"] == "SUCCEEDED"

    def test_ecr_repo_happy(self):
        m = MagicMock()
        m.describe_repositories.return_value = {"repositories": [{
            "repositoryArn": "arn:ecr:r", "repositoryUri": "uri",
            "createdAt": datetime.now(timezone.utc),
        }]}
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="ecr_repository", identifier="r")
        assert r["uri"] == "uri"

    def test_rds_instance_happy(self):
        m = MagicMock()
        m.describe_db_instances.return_value = {"DBInstances": [{
            "DBInstanceArn": "arn:rds", "DBInstanceStatus": "available",
            "Engine": "postgres", "Endpoint": {"Address": "host"},
        }]}
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="rds_instance", identifier="db")
        assert r["status"] == "available"
        assert r["endpoint"] == "host"

    def test_kms_key_happy(self):
        m = MagicMock()
        m.describe_key.return_value = {"KeyMetadata": {
            "Arn": "arn:kms:x", "KeyState": "Enabled",
            "KeyUsage": "GENERATE_VERIFY_MAC", "KeySpec": "HMAC_256",
        }}
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="kms_key", identifier="x")
        assert r["key_state"] == "Enabled"

    def test_secret_happy(self):
        m = MagicMock()
        m.describe_secret.return_value = {
            "ARN": "arn:sec:x", "Name": "n",
            "LastChangedDate": datetime.now(timezone.utc),
        }
        with _patch_aws(m):
            r = aws_resource.handler(resource_type="secret", identifier="x")
        assert r["name"] == "n"

    def test_unhandled_resource_type_raises(self):
        with pytest.raises(ToolUnknown):
            aws_resource.handler(resource_type="rdfdb", identifier="x")


# === TestCloudWatchLogs ====================================================

class TestCloudWatchLogs:
    def test_happy_path(self):
        m = MagicMock()
        m.filter_log_events.return_value = {
            "events": [{"timestamp": 1, "message": "hello", "logStreamName": "s"}],
            "nextToken": None,
        }
        with _patch_aws(m):
            r = cloudwatch_logs.handler(
                log_group="/g", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T00:30:00Z",
            )
        assert r["total_count"] == 1
        assert r["events"][0]["message"] == "hello"
        assert r["window_capped_to_24h"] is False

    def test_window_capped_to_24h(self):
        m = MagicMock()
        m.filter_log_events.return_value = {"events": []}
        with _patch_aws(m):
            r = cloudwatch_logs.handler(
                log_group="/g", start_time="2026-04-01T00:00:00Z",
                end_time="2026-05-01T00:00:00Z",
            )
        assert r["window_capped_to_24h"] is True

    def test_max_events_over_cap_raises(self):
        with pytest.raises(ToolUnknown):
            cloudwatch_logs.handler(
                log_group="/g", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z", max_events=10000,
            )

    def test_filter_pattern_passes_through(self):
        m = MagicMock()
        m.filter_log_events.return_value = {"events": []}
        with _patch_aws(m):
            cloudwatch_logs.handler(
                log_group="/g", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
                filter_pattern="ERROR",
            )
        kwargs = m.filter_log_events.call_args.kwargs
        assert kwargs.get("filterPattern") == "ERROR"

    def test_truncated_when_next_token(self):
        m = MagicMock()
        m.filter_log_events.return_value = {
            "events": [{"timestamp": i, "message": "x", "logStreamName": "s"} for i in range(50)],
            "nextToken": "abc",
        }
        with _patch_aws(m):
            r = cloudwatch_logs.handler(
                log_group="/g", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
            )
        assert r["truncated"] is True

    def test_message_truncated_to_4000_chars(self):
        big = "X" * 5000
        m = MagicMock()
        m.filter_log_events.return_value = {
            "events": [{"timestamp": 1, "message": big, "logStreamName": "s"}],
        }
        with _patch_aws(m):
            r = cloudwatch_logs.handler(
                log_group="/g", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
            )
        assert len(r["events"][0]["message"]) == 4000

    def test_forbidden_raises(self):
        m = MagicMock()
        m.filter_log_events.side_effect = _BotoClientErrorLike("AccessDeniedException")
        with _patch_aws(m), pytest.raises(ToolForbidden):
            cloudwatch_logs.handler(
                log_group="/g", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
            )

    def test_throttled_raises(self):
        m = MagicMock()
        m.filter_log_events.side_effect = _BotoClientErrorLike("ThrottlingException")
        with _patch_aws(m), pytest.raises(ToolThrottled):
            cloudwatch_logs.handler(
                log_group="/g", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
            )

    def test_max_events_clamped_to_min_one(self):
        m = MagicMock()
        m.filter_log_events.return_value = {"events": []}
        with _patch_aws(m):
            cloudwatch_logs.handler(
                log_group="/g", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z", max_events=0,
            )
        kwargs = m.filter_log_events.call_args.kwargs
        assert kwargs["limit"] >= 1


# === TestGitHub ============================================================

def _gh_resp(status=200, body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text or (body and __import__("json").dumps(body)) or ""
    r.json.return_value = body or {}
    return r


class TestGitHub:
    def setup_method(self):
        self._patch_token = patch.object(github, "_token", return_value="tok-test")
        self._patch_token.start()

    def teardown_method(self):
        self._patch_token.stop()

    def test_repo_enum_rejects_unknown_repo_via_schema(self):
        # The registry would catch this; but the handler also defends defensively
        with pytest.raises(ToolUnknown):
            github.handler(operation="read_file", repo="evil/repo", path="x")

    def test_read_file_happy(self):
        import base64
        body = {"path": "README.md", "sha": "abc",
                "size": 5, "content": base64.b64encode(b"hello").decode()}
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=body)
            r = github.handler(operation="read_file",
                               repo="iangreen74/aria-platform", path="README.md")
        assert r["content"] == "hello"
        assert r["sha"] == "abc"

    def test_read_pr_happy(self):
        body = {"title": "T", "body": "B", "state": "open", "merged": False,
                "head": {"ref": "feat/x"}, "base": {"ref": "main"}}
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=body)
            r = github.handler(operation="read_pr",
                               repo="iangreen74/iangreen74-nexus-platform",
                               pr_number=42)
        assert r["title"] == "T"
        assert r["head_ref"] == "feat/x"

    def test_list_commits_happy(self):
        body = [{"sha": "abc",
                 "commit": {"message": "msg",
                            "author": {"name": "A", "date": "d"}}}]
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=body)
            r = github.handler(operation="list_commits",
                               repo="iangreen74/aria-platform")
        assert r["commits"][0]["sha"] == "abc"

    def test_list_workflow_runs_happy(self):
        body = {"workflow_runs": [{"id": 1, "name": "CI", "status": "completed",
                                   "conclusion": "success", "created_at": "t",
                                   "html_url": "url"}]}
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=body)
            r = github.handler(operation="list_workflow_runs",
                               repo="iangreen74/aria-platform")
        assert r["workflow_runs"][0]["conclusion"] == "success"

    def test_403_forbidden(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(403, text="rate limit")
            with pytest.raises(ToolForbidden):
                github.handler(operation="read_pr",
                               repo="iangreen74/aria-platform", pr_number=1)

    def test_404_not_found(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(404, text="missing")
            with pytest.raises(ToolNotFound):
                github.handler(operation="read_file",
                               repo="iangreen74/aria-platform",
                               path="nope")

    def test_429_throttled(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(429, text="slow down")
            with pytest.raises(ToolThrottled):
                github.handler(operation="list_commits",
                               repo="iangreen74/aria-platform")

    def test_read_file_missing_path_raises(self):
        with pytest.raises(ToolUnknown):
            github.handler(operation="read_file",
                           repo="iangreen74/aria-platform")

    def test_read_pr_missing_number_raises(self):
        with pytest.raises(ToolUnknown):
            github.handler(operation="read_pr",
                           repo="iangreen74/aria-platform")

    def test_unknown_operation_raises(self):
        with pytest.raises(ToolUnknown):
            github.handler(operation="delete_repo",
                           repo="iangreen74/aria-platform")


# === TestPipelineTruth =====================================================

class TestPipelineTruth:
    def test_list_executions_happy(self):
        body = {"executions": [{"arn": "a", "verdict": {"kind": "GENUINE_SUCCESS"}}]}
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=body)
            r = pipeline_truth.handler(operation="list_executions", limit=10)
        assert r["executions"][0]["verdict"]["kind"] == "GENUINE_SUCCESS"

    def test_execution_detail_happy(self):
        body = {"arn": "a", "verdict": {"kind": "STUB_TERMINATION"}}
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=body)
            r = pipeline_truth.handler(operation="execution_detail",
                                       execution_arn="arn:exec")
        assert r["verdict"]["kind"] == "STUB_TERMINATION"

    def test_execution_detail_requires_arn(self):
        with pytest.raises(ToolUnknown):
            pipeline_truth.handler(operation="execution_detail")

    def test_regional_quotas_happy(self):
        body = {"alb_per_region": {"limit": 50, "in_use": 19}}
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=body)
            r = pipeline_truth.handler(operation="regional_quotas")
        assert r["alb_per_region"]["limit"] == 50

    def test_unknown_operation_raises(self):
        with pytest.raises(ToolUnknown):
            pipeline_truth.handler(operation="delete_executions")

    def test_404_not_found(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(404, text="no such arn")
            with pytest.raises(ToolNotFound):
                pipeline_truth.handler(operation="execution_detail",
                                       execution_arn="missing")

    def test_403_forbidden(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(403, text="auth fail")
            with pytest.raises(ToolForbidden):
                pipeline_truth.handler(operation="list_executions")

    def test_base_url_env_override(self, monkeypatch):
        monkeypatch.setenv("OVERWATCH_V2_API_URL", "http://elsewhere:9999")
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={"executions": []})
            pipeline_truth.handler(operation="list_executions")
        called_url = ctx.get.call_args.args[0]
        assert called_url.startswith("http://elsewhere:9999")

    def test_limit_clamped(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={"executions": []})
            pipeline_truth.handler(operation="list_executions", limit=99999)
        params = ctx.get.call_args.kwargs.get("params") or {}
        assert params["limit"] <= 200


# === TestEngineeringOntology ===============================================

def _fake_ontology_module(get_obj=None, list_objs=None, query_fn=None):
    mod = MagicMock()
    mod.get_object = MagicMock(return_value=get_obj)
    mod.list_objects_by_type = MagicMock(return_value=list_objs or [])
    mod.query = MagicMock(return_value=query_fn or [])
    return mod


class TestEngineeringOntology:
    def test_get_object_happy(self):
        fake = _fake_ontology_module(get_obj={"id": "x", "object_type": "Hypothesis", "claim": "c"})
        with patch.dict(sys.modules, {"nexus.overwatch_v2.ontology": fake}):
            r = engineering_ontology.handler(operation="get_object", object_id="x")
        assert r["object"]["id"] == "x"

    def test_get_object_not_found(self):
        fake = _fake_ontology_module(get_obj=None)
        with patch.dict(sys.modules, {"nexus.overwatch_v2.ontology": fake}):
            with pytest.raises(ToolNotFound):
                engineering_ontology.handler(operation="get_object", object_id="missing")

    def test_get_object_missing_id(self):
        with pytest.raises(ToolUnknown):
            engineering_ontology.handler(operation="get_object")

    def test_list_objects_happy(self):
        fake = _fake_ontology_module(list_objs=[{"id": "a"}, {"id": "b"}])
        with patch.dict(sys.modules, {"nexus.overwatch_v2.ontology": fake}):
            r = engineering_ontology.handler(operation="list_objects_by_type",
                                             object_type="Hypothesis")
        assert len(r["objects"]) == 2

    def test_list_objects_missing_type(self):
        with pytest.raises(ToolUnknown):
            engineering_ontology.handler(operation="list_objects_by_type")

    def test_list_objects_limit_clamped(self):
        fake = _fake_ontology_module(list_objs=[])
        with patch.dict(sys.modules, {"nexus.overwatch_v2.ontology": fake}):
            engineering_ontology.handler(operation="list_objects_by_type",
                                         object_type="Hypothesis", limit=999999)
        kwargs = fake.list_objects_by_type.call_args.kwargs
        assert kwargs["limit"] <= 200

    def test_query_neighbors_happy(self):
        fake = _fake_ontology_module(
            get_obj={"id": "x", "object_type": "Hypothesis"},
            query_fn=[{"edge_type": "SUPPORTS", "neighbor": {"id": "ev1"}}],
        )
        with patch.dict(sys.modules, {"nexus.overwatch_v2.ontology": fake}):
            r = engineering_ontology.handler(operation="query_neighbors",
                                             object_id="x")
        assert r["anchor_type"] == "Hypothesis"
        assert len(r["neighbors"]) == 1

    def test_query_neighbors_anchor_missing(self):
        fake = _fake_ontology_module(get_obj=None)
        with patch.dict(sys.modules, {"nexus.overwatch_v2.ontology": fake}):
            with pytest.raises(ToolNotFound):
                engineering_ontology.handler(operation="query_neighbors",
                                             object_id="ghost")

    def test_unknown_operation_raises(self):
        with pytest.raises(ToolUnknown):
            engineering_ontology.handler(operation="delete_all")

    def test_response_strips_underscore_prefixed_fields(self):
        fake = _fake_ontology_module(
            get_obj={"id": "x", "object_type": "T", "_action_event_id": "secret"}
        )
        with patch.dict(sys.modules, {"nexus.overwatch_v2.ontology": fake}):
            r = engineering_ontology.handler(operation="get_object", object_id="x")
        assert "_action_event_id" not in r["object"]


# === TestOverwatchMetrics ==================================================

class TestOverwatchMetrics:
    def test_happy_with_datapoints(self):
        m = MagicMock()
        m.get_metric_statistics.return_value = {
            "Datapoints": [
                {"Timestamp": datetime.now(timezone.utc), "Sum": 5.0, "Unit": "Count"},
                {"Timestamp": datetime.now(timezone.utc) + timedelta(minutes=5),
                 "Sum": 7.0, "Unit": "Count"},
            ]
        }
        with _patch_aws(m):
            r = overwatch_metrics.handler(
                metric_name="reasoner.tool_calls",
                start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
            )
        assert r["count"] == 2
        assert r["statistic"] == "Sum"

    def test_empty_datapoints_is_valid(self):
        m = MagicMock()
        m.get_metric_statistics.return_value = {"Datapoints": []}
        with _patch_aws(m):
            r = overwatch_metrics.handler(
                metric_name="reasoner.token_cost",
                start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
            )
        assert r["count"] == 0
        assert r["datapoints"] == []

    def test_period_below_minimum_raises(self):
        with pytest.raises(ToolUnknown):
            overwatch_metrics.handler(
                metric_name="x", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z", period_seconds=30,
            )

    def test_invalid_statistic_raises(self):
        with pytest.raises(ToolUnknown):
            overwatch_metrics.handler(
                metric_name="x", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z", statistic="Median",
            )

    def test_end_before_start_raises(self):
        with pytest.raises(ToolUnknown):
            overwatch_metrics.handler(
                metric_name="x", start_time="2026-04-24T01:00:00Z",
                end_time="2026-04-24T00:00:00Z",
            )

    def test_dimensions_must_be_dict(self):
        with pytest.raises(ToolUnknown):
            overwatch_metrics.handler(
                metric_name="x", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z", dimensions=["not", "a", "dict"],
            )

    def test_default_namespace_is_overwatch_v2(self):
        m = MagicMock()
        m.get_metric_statistics.return_value = {"Datapoints": []}
        with _patch_aws(m):
            overwatch_metrics.handler(
                metric_name="x", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
            )
        assert m.get_metric_statistics.call_args.kwargs["Namespace"] == "Overwatch/V2"

    def test_dimensions_passed_through(self):
        m = MagicMock()
        m.get_metric_statistics.return_value = {"Datapoints": []}
        with _patch_aws(m):
            overwatch_metrics.handler(
                metric_name="x", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
                dimensions={"Tool": "read_aws_resource"},
            )
        dims = m.get_metric_statistics.call_args.kwargs["Dimensions"]
        assert {"Name": "Tool", "Value": "read_aws_resource"} in dims

    def test_forbidden_raises(self):
        m = MagicMock()
        m.get_metric_statistics.side_effect = _BotoClientErrorLike("AccessDenied")
        with _patch_aws(m), pytest.raises(ToolForbidden):
            overwatch_metrics.handler(
                metric_name="x", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
            )

    def test_throttled_raises(self):
        m = MagicMock()
        m.get_metric_statistics.side_effect = _BotoClientErrorLike("ThrottlingException")
        with _patch_aws(m), pytest.raises(ToolThrottled):
            overwatch_metrics.handler(
                metric_name="x", start_time="2026-04-24T00:00:00Z",
                end_time="2026-04-24T01:00:00Z",
            )


# === TestErrorMapping ======================================================

class TestErrorMapping:
    def test_map_access_denied(self):
        e = _BotoClientErrorLike("AccessDenied")
        assert isinstance(map_boto_error(e), ToolForbidden)

    def test_map_resource_not_found(self):
        e = _BotoClientErrorLike("ResourceNotFoundException")
        assert isinstance(map_boto_error(e), ToolNotFound)

    def test_map_throttling(self):
        e = _BotoClientErrorLike("ThrottlingException")
        assert isinstance(map_boto_error(e), ToolThrottled)

    def test_map_unknown_code(self):
        e = _BotoClientErrorLike("UnseenCodeXYZ")
        assert isinstance(map_boto_error(e), ToolUnknown)

    def test_map_no_code(self):
        e = Exception("just a string")
        assert isinstance(map_boto_error(e), ToolUnknown)


# === TestRegistration ======================================================

def _fake_registry_module():
    mod = MagicMock()
    mod.RISK_LOW = "low"
    mod.RISK_MEDIUM = "medium"
    mod.RISK_HIGH = "high"

    class _ToolSpec:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    mod.ToolSpec = _ToolSpec
    mod.register = MagicMock()
    mod.list_tools = MagicMock(return_value=[])
    return mod


class TestRegistration:
    def test_lazy_import_pattern(self):
        # tool modules should not pull in the registry at import time
        for name in (
            "nexus.overwatch_v2.tools.read_tools.aws_resource",
            "nexus.overwatch_v2.tools.read_tools.cloudwatch_logs",
            "nexus.overwatch_v2.tools.read_tools.github",
            "nexus.overwatch_v2.tools.read_tools.pipeline_truth",
            "nexus.overwatch_v2.tools.read_tools.engineering_ontology",
            "nexus.overwatch_v2.tools.read_tools.overwatch_metrics",
        ):
            assert name in sys.modules
        # registry only enters sys.modules when register_tool() runs
        # (we don't assert it's absent because other tests may have imported it,
        # but the tool modules' source should not contain top-level imports of it)
        for mod_name in (
            "nexus.overwatch_v2.tools.read_tools.aws_resource",
            "nexus.overwatch_v2.tools.read_tools.cloudwatch_logs",
        ):
            mod = sys.modules[mod_name]
            assert "registry" not in dir(mod)

    def test_register_all_seven_tools(self):
        # Track Q added list_aws_resources alongside the original 6.
        fake = _fake_registry_module()
        with patch.dict(sys.modules, {"nexus.overwatch_v2.tools.registry": fake}):
            from nexus.overwatch_v2.tools.read_tools._registration import register_all_read_tools
            register_all_read_tools()
        assert fake.register.call_count == 7
        names = {call.args[0].name for call in fake.register.call_args_list}
        assert names == {
            "read_aws_resource", "read_cloudwatch_logs", "read_github",
            "query_pipeline_truth", "query_engineering_ontology",
            "read_overwatch_metrics", "list_aws_resources",
        }

    def test_all_tools_marked_read_only(self):
        fake = _fake_registry_module()
        with patch.dict(sys.modules, {"nexus.overwatch_v2.tools.registry": fake}):
            from nexus.overwatch_v2.tools.read_tools._registration import register_all_read_tools
            register_all_read_tools()
        for call in fake.register.call_args_list:
            assert call.args[0].requires_approval is False
            assert call.args[0].risk_level == "low"

    def test_individual_register_tool_calls_register_once(self):
        fake = _fake_registry_module()
        with patch.dict(sys.modules, {"nexus.overwatch_v2.tools.registry": fake}):
            aws_resource.register_tool()
        assert fake.register.call_count == 1

    def test_registration_idempotent_with_same_name(self):
        fake = _fake_registry_module()
        with patch.dict(sys.modules, {"nexus.overwatch_v2.tools.registry": fake}):
            aws_resource.register_tool()
            aws_resource.register_tool()
        # registry.register is called twice; idempotency is the registry's job
        # (it overwrites by name). We assert our tool just calls it cleanly.
        assert fake.register.call_count == 2
        names = [c.args[0].name for c in fake.register.call_args_list]
        assert names == ["read_aws_resource", "read_aws_resource"]
