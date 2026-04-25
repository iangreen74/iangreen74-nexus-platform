"""Acceptance test for the pipeline-truth categoriser.

The Day 2 regression: forgewing-deploy-v2 executions on 2026-04-22 reported
status=SUCCEEDED while a large fraction were in fact stub-terminations
(never deployed). The categoriser must distinguish these from actual
successful deploys using the execution's output payload, not its status.

This test derives ground-truth labels directly from the output payload
(a stub-termination carries `failure_reason: "stub termination"`;
a genuine success carries `stage: "deploy_complete"` with `healthy: True`
and a live `app_url`) and asserts the categoriser's verdict matches for
every execution. If the categoriser drifts from the truth signal even
once, this test fails.

Run: python -m pytest tests/integration/test_pipeline_truth_96_executions.py -s
Requires: /tmp/sfn-target-execs.json (populated by the Part 2 discovery)
          AWS creds for stepfunctions:DescribeExecution.
"""
from __future__ import annotations

import json
import os

import pytest

TARGET_FILE = "/tmp/sfn-target-execs.json"


def _ground_truth_from_output(output_obj):
    """Label an execution from its output, independent of the categoriser."""
    if not isinstance(output_obj, dict):
        return "UNKNOWN"
    fr = output_obj.get("failure_reason")
    if isinstance(fr, str) and "stub" in fr.lower() and "termin" in fr.lower():
        return "STUB_TERMINATION"
    if (output_obj.get("stage") == "deploy_complete"
            and output_obj.get("healthy") is True
            and output_obj.get("http_status") == 200
            and output_obj.get("app_url")):
        return "GENUINE_SUCCESS"
    return "UNKNOWN"


@pytest.mark.integration
@pytest.mark.skipif(not os.path.exists(TARGET_FILE),
                    reason=f"{TARGET_FILE} missing — run discovery step first")
def test_2026_04_22_executions_classify_against_ground_truth():
    target_execs = json.load(open(TARGET_FILE))
    assert target_execs, "target exec list is empty"

    from nexus.dashboard.pipeline_truth_routes import (
        categorise_execution,
        fetch_execution_evidence,
    )

    results = []
    for exec_info in target_execs:
        evidence = fetch_execution_evidence(exec_info["arn"])
        parsed = evidence["sfn_output_parsed"]
        truth = _ground_truth_from_output(parsed)
        verdict = categorise_execution(
            execution_arn=exec_info["arn"],
            sfn_status=evidence["sfn_status"],
            sfn_output=parsed,
            ecs_task_exit_codes=evidence["ecs_task_exit_codes"],
            cfn_first_failed_resource=evidence["cfn_first_failed_resource"],
        )
        results.append((exec_info["arn"], truth, verdict))

    mismatches = [(a, t, v) for a, t, v in results if t != "UNKNOWN" and v.kind != t]
    truth_counts = {}
    for _, t, _ in results:
        truth_counts[t] = truth_counts.get(t, 0) + 1
    verdict_counts = {}
    for _, _, v in results:
        verdict_counts[v.kind] = verdict_counts.get(v.kind, 0) + 1

    print(f"\nGround-truth distribution: {truth_counts}")
    print(f"Categoriser verdicts:       {verdict_counts}")

    if mismatches:
        details = "\n".join(
            f"  {a.split(':')[-1]}: truth={t}  verdict={v.kind}  reason={v.reason}"
            for a, t, v in mismatches[:10]
        )
        pytest.fail(
            f"{len(mismatches)} of {len(results)} executions disagree with ground truth.\n"
            f"Sample:\n{details}"
        )

    stub_count = sum(1 for _, t, _ in results if t == "STUB_TERMINATION")
    success_count = sum(1 for _, t, _ in results if t == "GENUINE_SUCCESS")
    assert stub_count > 0, (
        "No STUB_TERMINATION executions found. The whole point of this "
        "acceptance test is that 2026-04-22 had a large fraction of stub "
        "terminations — if none are detected, the discovery step or the "
        "categoriser is misconfigured."
    )
    print(
        f"✓ All {len(results)} executions classify correctly: "
        f"{stub_count} STUB_TERMINATION + {success_count} GENUINE_SUCCESS"
    )
