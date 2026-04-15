"""Tests for the five intelligence improvements."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus import findings as findings_mod  # noqa: E402
from nexus.capabilities import investigation, sprint_context  # noqa: E402
from nexus.reasoning import triage  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


# --- 1. Triage suppression --------------------------------------------------


def _make_report(**overrides):
    base = {
        "tenant_id": "forge-test",
        "overall_status": "critical",
        "deploy_stuck": True,
        "mission_stage": "executing",
        "pipeline": {"tasks_in_review": 0, "tasks_in_progress": 0,
                     "stuck_task_count": 0},
        "conversation": {"message_count": 10, "inactive": False},
        "deployment": {"healthy": False},
    }
    base.update(overrides)
    return base


def test_suppress_pr_blocked_pipeline():
    r = _make_report(pipeline={"tasks_in_review": 1, "tasks_in_progress": 0})
    decision = triage.triage_tenant_health(r)
    assert decision.action == "noop"
    assert "PR-blocked" in decision.reasoning
    assert decision.metadata.get("deploy_stuck_suppressed")


def test_suppress_test_tenant_complete_stage():
    r = _make_report(mission_stage="complete",
                     conversation={"message_count": 1, "inactive": False})
    decision = triage.triage_tenant_health(r)
    assert decision.action == "noop"
    assert "test/demo" in decision.reasoning


def test_suppress_idle_tenant_with_no_active_work():
    r = _make_report(conversation={"message_count": 5, "inactive": True},
                     pipeline={"tasks_in_review": 0, "tasks_in_progress": 0})
    decision = triage.triage_tenant_health(r)
    assert decision.action == "noop"
    assert "idle" in decision.reasoning


def test_deploy_stuck_still_fires_when_not_suppressed():
    r = _make_report(conversation={"message_count": 50, "inactive": False},
                     pipeline={"tasks_in_review": 0, "tasks_in_progress": 2})
    decision = triage.triage_tenant_health(r)
    assert decision.action != "noop"


# --- 2. Dynamic release readiness -------------------------------------------


def test_release_checklist_get_endpoint():
    sprint_context.reset()
    resp = client.get("/api/release-checklist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 9


def test_release_checklist_mark_endpoint():
    sprint_context.reset()
    resp = client.post("/api/release-checklist/mark/incognito_walkthrough",
                       json={"status": "done"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"]
    assert body["checklist"]["done"] >= 6


def test_release_checklist_mark_unknown_item():
    sprint_context.reset()
    resp = client.post("/api/release-checklist/mark/does_not_exist",
                       json={"status": "done"})
    assert resp.status_code == 404


def test_release_checklist_mark_invalid_status():
    sprint_context.reset()
    resp = client.post("/api/release-checklist/mark/sfs_flow",
                       json={"status": "nonsense"})
    assert resp.status_code == 400


def test_release_checklist_clear_override():
    sprint_context.reset()
    sprint_context.set_item_status("sfs_flow", "done")
    resp = client.post("/api/release-checklist/mark/sfs_flow",
                       json={"status": "clear"})
    assert resp.status_code == 200
    assert resp.json()["cleared"] is True


def test_sprint_items_carry_source_tag():
    sprint_context.reset()
    items = sprint_context.get_items()
    by_id = {i["id"]: i for i in items}
    assert by_id["incognito_walkthrough"]["source"] == "baseline"
    # In local mode live checks return None → baseline status.
    assert by_id["pr_generation"]["source"] == "baseline"


def test_manual_override_beats_live_check():
    sprint_context.reset()
    sprint_context.set_item_status("pr_generation", "blocked")
    items = {i["id"]: i for i in sprint_context.get_items()}
    assert items["pr_generation"]["status"] == "blocked"
    assert items["pr_generation"]["source"] == "manual"


# --- 3. CloudWatch traceback parsing ---------------------------------------


def test_parse_traceback_extracts_deepest_frame():
    msg = (
        "forgescaler.deploy ERROR - something bad\n"
        'Traceback (most recent call last):\n'
        '  File "/app/aria/remote_engineer/deployment/deploy_monitor.py", line 47, in scan_forge_workflows\n'
        '    result = items[key]\n'
        "TypeError: list indices must be integers or slices, not str"
    )
    frame = investigation._parse_traceback(msg)
    assert frame is not None
    assert frame["file"] == "aria/remote_engineer/deployment/deploy_monitor.py"
    assert frame["line"] == 47
    assert frame["function"] == "scan_forge_workflows"
    assert "TypeError" in frame["exc"]


def test_parse_traceback_returns_none_without_frame():
    assert investigation._parse_traceback("just a plain error message") is None


def test_logger_summary_extracts_first_line():
    msg = "forgescaler.deploy.monitor ERROR - list indices must be integers"
    assert "forgescaler.deploy.monitor" in investigation._logger_summary(msg)
    assert "ERROR" in investigation._logger_summary(msg)


# --- 4. Findings dedup + classification -------------------------------------


def test_fingerprint_is_stable_across_count_variants():
    a = findings_mod.Finding(summary="19 BriefEntry orphans found",
                              category="data_fix", severity="warning")
    b = findings_mod.Finding(summary="20 BriefEntry orphans found",
                              category="data_fix", severity="warning")
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_differs_across_categories():
    a = findings_mod.Finding(summary="X", category="code_fix")
    b = findings_mod.Finding(summary="X", category="data_fix")
    assert a.fingerprint() != b.fingerprint()


def test_classify_new_then_ongoing_then_resolved():
    findings_mod.reset()
    f1 = findings_mod.Finding(summary="BriefEntry orphans",
                               category="data_fix")

    g = findings_mod.track_and_classify([f1])
    assert len(g["new"]) == 1 and len(g["ongoing"]) == 0

    g = findings_mod.track_and_classify([f1])
    assert len(g["new"]) == 0 and len(g["ongoing"]) == 1
    assert g["ongoing"][0]["cycles"] == 2

    g = findings_mod.track_and_classify([])
    assert len(g["resolved"]) == 1


def test_format_finding_leads_with_file_and_action():
    f = findings_mod.Finding(
        summary="TypeError in scan_forge_workflows",
        file="aria/deploy_monitor.py", line=47, function="scan_forge_workflows",
        action="Add isinstance check.", prompt="Fix deploy_monitor.py",
        category="code_fix",
    )
    text = findings_mod.format_finding(f)
    assert text.startswith("FIX: aria/deploy_monitor.py:47 scan_forge_workflows()")
    assert "ACTION: Add isinstance check." in text
    assert 'PROMPT: "Fix deploy_monitor.py"' in text


def test_format_report_has_all_three_sections():
    findings_mod.reset()
    f1 = findings_mod.Finding(summary="BriefEntry orphans detected",
                               category="data_fix")
    f2 = findings_mod.Finding(summary="DeploymentProgress drift alert",
                               category="config")
    findings_mod.track_and_classify([f1])
    findings_mod.track_and_classify([f1, f2])
    g = findings_mod.track_and_classify([f2])
    text = findings_mod.format_report(g)
    assert "### ONGOING" in text
    assert "### RESOLVED" in text


def test_from_cloudwatch_entry_builds_code_fix():
    entry = {
        "source": "/aria/daemon", "timestamp": 123456,
        "message": "x", "summary": "daemon ERROR",
        "file": "nexus/foo.py", "line": 42, "function": "do_it",
        "exc": "TypeError: list indices must be integers",
    }
    f = findings_mod.from_cloudwatch_entry(entry)
    assert f.category == "code_fix"
    assert f.file == "nexus/foo.py"
    assert "TypeError" in f.summary
    assert f.prompt != ""


def test_classify_endpoint_end_to_end():
    findings_mod.reset()
    resp = client.post("/api/findings/classify", json={
        "findings": [
            {"summary": "BriefEntry orphans (19)", "category": "data_fix",
             "severity": "warning"},
            {"summary": "Deploy monitor TypeError", "category": "code_fix",
             "file": "aria/deploy_monitor.py", "line": 47,
             "action": "Add guard.", "prompt": "Fix it"},
        ]
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_count"] == 2
    assert "FIX:" in body["report"]
