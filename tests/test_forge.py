"""
Tests for the Forge engine — aria_repo, fix_generator, deploy_manager.

All tests run in NEXUS_MODE=local; no real GitHub or AWS calls happen.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.forge import aria_repo, deploy_manager, fix_generator  # noqa: E402
from nexus.forge.aria_repo import FileChange  # noqa: E402


# --- aria_repo ---------------------------------------------------------------
def test_read_file_returns_mock_in_local_mode():
    contents = aria_repo.read_file("aria/intelligence/quality_gate.py")
    assert contents is not None
    assert "[local mock]" in contents


def test_list_recent_commits_local():
    commits = aria_repo.list_recent_commits()
    assert len(commits) >= 1
    assert "sha" in commits[0]


def test_list_open_prs_local():
    prs = aria_repo.list_open_prs()
    assert isinstance(prs, list)


def test_list_overwatch_prs_filters_by_label():
    # In local mock, the sample PR has no overwatch-fix label, so result is []
    overwatch_prs = aria_repo.list_overwatch_prs()
    assert isinstance(overwatch_prs, list)


def test_get_workflow_status_local():
    status = aria_repo.get_workflow_status("ci.yml")
    assert status["workflow"] == "ci.yml"
    assert status["status"] == "completed"


def test_create_fix_pr_returns_mock_in_local():
    result = aria_repo.create_fix_pr(
        branch_name="overwatch/test-branch",
        file_changes=[FileChange(path="x.py", new_content="print('hi')\n")],
        title="test",
        body="test body",
    )
    assert result.get("mock") is True
    assert result["branch"] == "overwatch/test-branch"


# --- fix_generator -----------------------------------------------------------
def test_known_templates_listed():
    templates = fix_generator.list_known_fix_templates()
    names = {t["pattern"] for t in templates}
    assert "bedrock_json_parse" in names
    assert "bare_aws_to_module" in names
    assert "github_app_owner_match" in names


def test_propose_fix_unknown_pattern():
    result = fix_generator.propose_fix("not_a_real_pattern")
    assert result.get("error") == "unknown_pattern"


def test_validate_fix_rejects_empty():
    ok, reason = fix_generator.validate_fix([])
    assert ok is False
    assert "no changes" in reason


def test_validate_fix_rejects_noop():
    change = FileChange(path="x.py", new_content="same", old_content="same")
    ok, reason = fix_generator.validate_fix([change])
    assert ok is False
    assert "no-op" in reason


def test_validate_fix_accepts_real_diff():
    change = FileChange(path="x.py", new_content="new", old_content="old")
    ok, _ = fix_generator.validate_fix([change])
    assert ok is True


def test_apply_transform_fence_strip():
    original = "result = json.loads(text)\n"
    new = fix_generator._apply_transform("fence_strip", original, "json.loads(")
    assert new is not None
    assert "_strip_fences" in new
    assert "json.loads(_strip_fences(text))" in new


def test_apply_transform_idempotent():
    """Running the same transform twice shouldn't double-apply."""
    once = fix_generator._apply_transform("fence_strip", "json.loads(x)", "json.loads(")
    assert once is not None
    twice = fix_generator._apply_transform("fence_strip", once, "json.loads(")
    assert twice is None  # already applied → no further change


# --- deploy_manager ----------------------------------------------------------
def test_deploy_service_local():
    result = deploy_manager.deploy_service("aria-daemon")
    assert result["service"] == "aria-daemon"
    assert result.get("mock") is True


def test_deploy_via_ci_local():
    result = deploy_manager.deploy_via_ci("deploy.yml")
    assert result["triggered"] is True
    assert result["workflow"] == "deploy.yml"


def test_get_deploy_status_local():
    status = deploy_manager.get_deploy_status("aria-console")
    assert status["service"] == "aria-console"
    assert status["status"] == "PRIMARY"


def test_wait_for_stable_local():
    result = deploy_manager.wait_for_stable("aria-console", timeout=1)
    assert result["stable"] is True


def test_rollback_service_local():
    result = deploy_manager.rollback_service("aria-daemon")
    assert result["service"] == "aria-daemon"
    assert result.get("mock") is True
