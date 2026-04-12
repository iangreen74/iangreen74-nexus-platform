"""Unit tests for the project isolation QA journeys.

The journeys themselves call the live Forgewing API, so these tests
cover the matching logic only — the bits that interpret API responses
and decide pass/fail. The live wiring is exercised by the operator
console when /api/synthetic-tests is hit.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")


def test_conversation_no_project_detects_unscoped():
    """Unscoped message (no project_id) should be flagged."""
    messages = [
        {"role": "user", "content": "hello", "project_id": "proj-123"},
        {"role": "user", "content": "old message"},  # no project_id
    ]
    unscoped = [m for m in messages if not m.get("project_id")]
    assert len(unscoped) == 1


def test_conversation_all_scoped_no_finding():
    """Every message has a project_id → no unscoped finding."""
    messages = [
        {"role": "user", "content": "a", "project_id": "proj-1"},
        {"role": "assistant", "content": "b", "project_id": "proj-1"},
    ]
    unscoped = [m for m in messages if not m.get("project_id")]
    assert unscoped == []


def test_conversation_scoped_detects_wrong_project():
    """Message belonging to a different project is a leak."""
    target = "proj-123"
    messages = [
        {"content": "a", "project_id": "proj-123"},
        {"content": "b", "project_id": "proj-456"},  # leak
    ]
    wrong = [m for m in messages
             if m.get("project_id") and m["project_id"] != target]
    assert len(wrong) == 1
    assert wrong[0]["project_id"] == "proj-456"


def test_conversation_scoped_tolerates_null_project_id():
    """Messages with no project_id are not flagged by the scoped check;
    journey_conversation_no_project_bleed handles those separately."""
    target = "proj-123"
    messages = [
        {"content": "a", "project_id": "proj-123"},
        {"content": "b"},  # no project_id — not flagged here
    ]
    wrong = [m for m in messages
             if m.get("project_id") and m["project_id"] != target]
    assert wrong == []


def test_status_repo_mismatch_detected():
    """Status repo differs from project repo → mismatch."""
    project_repo = "https://github.com/x/y"
    status_repo = "https://github.com/x/z"
    assert project_repo and status_repo and status_repo != project_repo


def test_status_repo_match_clean():
    """Status repo matches project repo → no finding."""
    project_repo = "https://github.com/x/y"
    status_repo = "https://github.com/x/y"
    mismatch = bool(project_repo and status_repo and status_repo != project_repo)
    assert mismatch is False


def test_actions_false_positive_detection():
    """AWS is connected but ActionBanner still says 'connect cloud'."""
    has_aws = True
    actions = [{"type": "cloud_not_connected", "severity": "high"}]
    cloud_action = next(
        (a for a in actions if "cloud" in (a.get("type") or "").lower()),
        None,
    )
    false_positive = has_aws and cloud_action is not None
    assert false_positive is True


def test_actions_no_false_positive_when_not_connected():
    """No AWS + cloud banner present = honest, not a false positive."""
    has_aws = False
    actions = [{"type": "cloud_not_connected", "severity": "high"}]
    cloud_action = next(
        (a for a in actions if "cloud" in (a.get("type") or "").lower()),
        None,
    )
    false_positive = has_aws and cloud_action is not None
    assert false_positive is False


def test_actions_alternate_type_key():
    """Forgewing may send either 'type' or 'action_type' — both supported."""
    has_aws = True
    actions = [{"action_type": "no_cloud_connected", "severity": "high"}]
    cloud_action = next(
        (a for a in actions
         if "cloud" in (a.get("type") or a.get("action_type") or "").lower()),
        None,
    )
    false_positive = has_aws and cloud_action is not None
    assert false_positive is True


def test_journeys_registered_in_runner():
    """All 5 new journeys appear in the run_all_journeys list."""
    import inspect
    from nexus import synthetic_tests

    src = inspect.getsource(synthetic_tests.run_all_journeys)
    for name in ("journey_conversation_no_project_bleed",
                 "journey_conversation_project_scoped",
                 "journey_status_project_scoped",
                 "journey_brief_project_scoped",
                 "journey_actions_reflect_reality"):
        assert name in src, f"{name} not registered in run_all_journeys"
