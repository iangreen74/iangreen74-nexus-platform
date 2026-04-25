"""Unit tests for pipeline-truth endpoint and categoriser.

The categoriser is the truth-first classifier; most cases live here.
Route tests exercise the FastAPI layer through a TestClient with mocked
AWS clients via the _client factory.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus.dashboard import pipeline_truth_routes as pt


# ---------- categoriser ---------------------------------------------------


def test_categorise_stub_termination_from_failure_reason_field():
    output = {"failure_reason": "stub termination", "recovered": False}
    v = pt.categorise_execution("arn:x", "SUCCEEDED", output)
    assert v.kind == "STUB_TERMINATION"
    assert "stub" in v.reason.lower()
    assert v.signals["failure_reason"] == "stub termination"


def test_categorise_stub_termination_with_raw_json_string_output():
    raw = json.dumps({"failure_reason": "stub termination", "recovered": False})
    v = pt.categorise_execution("arn:x", "SUCCEEDED", raw)
    assert v.kind == "STUB_TERMINATION"


def test_categorise_genuine_success_empty_output():
    v = pt.categorise_execution("arn:x", "SUCCEEDED", {"ready": True})
    assert v.kind == "GENUINE_SUCCESS"
    assert v.signals["sfn_status"] == "SUCCEEDED"


def test_categorise_genuine_success_with_no_output_still_ok():
    v = pt.categorise_execution("arn:x", "SUCCEEDED", None)
    assert v.kind == "GENUINE_SUCCESS"


def test_categorise_genuine_failure_status_failed():
    v = pt.categorise_execution("arn:x", "FAILED", {"failure_reason": "real error"})
    assert v.kind == "GENUINE_FAILURE"


def test_categorise_genuine_failure_status_timed_out():
    v = pt.categorise_execution("arn:x", "TIMED_OUT", None)
    assert v.kind == "GENUINE_FAILURE"


def test_categorise_in_progress():
    v = pt.categorise_execution("arn:x", "RUNNING", None)
    assert v.kind == "IN_PROGRESS"


def test_categorise_ecs_task_failure_via_explicit_exit_codes():
    v = pt.categorise_execution(
        "arn:x", "SUCCEEDED", {}, ecs_task_exit_codes=[0, 0, 137]
    )
    assert v.kind == "ECS_TASK_FAILURE"
    assert 137 in v.signals["ecs_exit_codes"]


def test_categorise_cfn_failure_takes_priority_over_success_status():
    failed = {"LogicalResourceId": "AppService",
              "ResourceStatus": "CREATE_FAILED",
              "ResourceStatusReason": "image pull failure"}
    v = pt.categorise_execution(
        "arn:x", "SUCCEEDED", {}, cfn_first_failed_resource=failed
    )
    assert v.kind == "CFN_FAILURE"
    assert "AppService" in v.reason


def test_categorise_stub_termination_wins_over_cfn_failure():
    """Stub termination is the most specific diagnosis — should take priority."""
    failed = {"LogicalResourceId": "X", "ResourceStatus": "CREATE_FAILED"}
    v = pt.categorise_execution(
        "arn:x", "SUCCEEDED",
        {"failure_reason": "stub termination"},
        cfn_first_failed_resource=failed,
    )
    assert v.kind == "STUB_TERMINATION"


def test_categorise_invalid_json_output_falls_through_to_success():
    v = pt.categorise_execution("arn:x", "SUCCEEDED", "{{not valid json")
    assert v.kind == "GENUINE_SUCCESS"


def test_categorise_unknown_when_status_unexpected():
    v = pt.categorise_execution("arn:x", "WEIRD_NEW_STATUS", None)
    assert v.kind == "UNKNOWN"


# ---------- ECS exit code extraction -------------------------------------


def test_extract_exit_codes_from_stack_error_cause_string():
    """Real SFN output embeds Cause as a JSON string containing Containers[]."""
    cause = json.dumps({"Containers": [
        {"Name": "stage", "ExitCode": 1},
        {"Name": "sidecar", "ExitCode": 0},
    ]})
    output = {"stack_error": {"Error": "States.TaskFailed", "Cause": cause}}
    assert pt._extract_ecs_exit_codes(output) == [1, 0]


def test_extract_exit_codes_with_no_stack_error():
    assert pt._extract_ecs_exit_codes({"ready": True}) == []


def test_extract_exit_codes_with_none():
    assert pt._extract_ecs_exit_codes(None) == []


def test_extract_exit_codes_when_cause_is_not_parseable():
    output = {"stack_error": {"Cause": "not-json-at-all"}}
    assert pt._extract_ecs_exit_codes(output) == []


def test_extract_exit_codes_with_dict_cause_already_parsed():
    """Some callers hand in already-parsed cause dicts."""
    output = {"stack_error": {"Cause": {"Containers": [{"ExitCode": 2}]}}}
    assert pt._extract_ecs_exit_codes(output) == [2]


# ---------- endpoint: executions list -------------------------------------


@pytest.fixture
def app_client():
    app = FastAPI()
    app.include_router(pt.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_quota_cache():
    pt._QUOTA_CACHE["data"] = None
    pt._QUOTA_CACHE["expires_at"] = 0.0
    yield


def _fake_sfn_client(list_resp, describe_resp_by_arn):
    m = MagicMock()
    m.list_executions.return_value = list_resp
    m.describe_execution.side_effect = lambda executionArn, **_: describe_resp_by_arn[
        executionArn
    ]
    return m


def test_list_executions_classifies_stub_termination(app_client):
    arn = "arn:exec:stub-1"
    stub_output = json.dumps({"failure_reason": "stub termination"})
    sfn = _fake_sfn_client(
        list_resp={"executions": [{"executionArn": arn, "name": "ex1",
                                    "status": "SUCCEEDED"}]},
        describe_resp_by_arn={arn: {"status": "SUCCEEDED", "output": stub_output,
                                      "input": "{}", "startDate": None,
                                      "stopDate": None}},
    )
    cfn = MagicMock()
    cfn.describe_stack_events.return_value = {"StackEvents": []}

    def fake_client(svc):
        return {"stepfunctions": sfn, "cloudformation": cfn}.get(svc, MagicMock())

    with patch.object(pt, "_client", side_effect=fake_client):
        body = app_client.get("/api/v2/pipeline-truth/executions").json()

    assert body["count"] == 1
    v = body["executions"][0]["verdict"]
    assert v["kind"] == "STUB_TERMINATION"


def test_list_executions_invalid_status_filter(app_client):
    r = app_client.get("/api/v2/pipeline-truth/executions?status_filter=BOGUS")
    assert r.status_code == 400


def test_list_executions_limit_bounds(app_client):
    assert app_client.get(
        "/api/v2/pipeline-truth/executions?limit=0").status_code == 422
    assert app_client.get(
        "/api/v2/pipeline-truth/executions?limit=500").status_code == 422


def test_list_executions_aws_error_returns_empty(app_client):
    sfn = MagicMock()
    sfn.list_executions.side_effect = RuntimeError("network down")

    def fake_client(svc):
        return sfn if svc == "stepfunctions" else MagicMock()

    with patch.object(pt, "_client", side_effect=fake_client):
        body = app_client.get("/api/v2/pipeline-truth/executions").json()
    assert body["count"] == 0
    assert "error" in body


# ---------- endpoint: execution detail ------------------------------------


def test_execution_detail_returns_verdict_and_evidence(app_client):
    arn = "arn:aws:states:us-east-1:418295677815:execution:sm:ex1"
    stub_output = json.dumps({
        "failure_reason": "stub termination",
        "stack_name": "forge-demo-abc",
        "stack_error": {"Cause": json.dumps({"Containers": [{"ExitCode": 1}]})},
    })
    sfn = MagicMock()
    sfn.describe_execution.return_value = {
        "status": "SUCCEEDED", "output": stub_output, "input": "{}",
        "startDate": datetime(2026, 4, 22, 1, 0, tzinfo=timezone.utc),
        "stopDate": datetime(2026, 4, 22, 1, 10, tzinfo=timezone.utc),
    }
    cfn = MagicMock()
    cfn.describe_stack_events.return_value = {"StackEvents": []}
    ecs = MagicMock()
    ecs.describe_tasks.return_value = {"tasks": []}
    ct = MagicMock()
    ct.lookup_events.return_value = {"Events": []}

    def fake_client(svc):
        return {"stepfunctions": sfn, "cloudformation": cfn,
                "ecs": ecs, "cloudtrail": ct}.get(svc, MagicMock())

    with patch.object(pt, "_client", side_effect=fake_client):
        body = app_client.get(f"/api/v2/pipeline-truth/executions/{arn}").json()
    assert body["verdict"]["kind"] == "STUB_TERMINATION"
    assert body["stack_name"] == "forge-demo-abc"
    assert body["cloudtrail_assume_role_events"] == []


def test_execution_detail_handles_describe_failure(app_client):
    arn = "arn:aws:states:us-east-1:418295677815:execution:sm:missing"
    sfn = MagicMock()
    sfn.describe_execution.side_effect = RuntimeError("ExecutionDoesNotExist")

    with patch.object(pt, "_client", return_value=sfn):
        r = app_client.get(f"/api/v2/pipeline-truth/executions/{arn}")
    assert r.status_code == 502


# ---------- endpoint: quotas ---------------------------------------------


def test_quotas_endpoint_aggregates_three_services(app_client):
    sq = MagicMock()
    sq.get_service_quota.side_effect = lambda ServiceCode, QuotaCode: {
        "Quota": {"Value": 100.0, "Unit": "None", "Adjustable": True}
    }

    def fake_client(svc):
        return sq if svc == "service-quotas" else MagicMock()

    with patch.object(pt, "_client", side_effect=fake_client):
        body = app_client.get("/api/v2/pipeline-truth/quotas").json()
    assert "alb" in body["quotas"]
    assert "eip" in body["quotas"]
    assert "lambda_concurrent" in body["quotas"]
    assert body["cached"] is False


def test_quotas_endpoint_caches_result(app_client):
    sq = MagicMock()
    sq.get_service_quota.return_value = {"Quota": {"Value": 50.0}}

    def fake_client(svc):
        return sq if svc == "service-quotas" else MagicMock()

    with patch.object(pt, "_client", side_effect=fake_client):
        first = app_client.get("/api/v2/pipeline-truth/quotas").json()
        second = app_client.get("/api/v2/pipeline-truth/quotas").json()
    assert first["cached"] is False
    assert second["cached"] is True


# ---------- path-param parsing for colon-bearing ARNs --------------------


def test_execution_arn_with_colons_is_parseable(app_client):
    """SFN execution arns contain multiple colons; the :path converter must preserve them."""
    arn = "arn:aws:states:us-east-1:418295677815:execution:sm:e1"
    sfn = MagicMock()
    sfn.describe_execution.return_value = {"status": "SUCCEEDED", "output": "{}",
                                            "input": "{}", "startDate": None,
                                            "stopDate": None}
    cfn = MagicMock()
    cfn.describe_stack_events.return_value = {"StackEvents": []}

    def fake_client(svc):
        return {"stepfunctions": sfn, "cloudformation": cfn}.get(svc, MagicMock())

    captured = {}
    orig = sfn.describe_execution

    def record(**kw):
        captured.update(kw)
        return orig.return_value

    sfn.describe_execution.side_effect = record

    with patch.object(pt, "_client", side_effect=fake_client):
        app_client.get(f"/api/v2/pipeline-truth/executions/{arn}")
    assert captured.get("executionArn") == arn
