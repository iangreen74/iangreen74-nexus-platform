"""Tests for pattern learner — the self-programming engine."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus import overwatch_graph  # noqa: E402
from nexus.reasoning.pattern_learner import (  # noqa: E402
    GRADUATION_THRESHOLD,
    CandidatePattern,
    _candidates,
    approve_candidate,
    capture_resolution,
    find_matching_candidate,
    reject_candidate,
)


def _cleanup():
    _candidates.clear()
    overwatch_graph.reset_local_store()


def test_capture_creates_candidate():
    _cleanup()
    cp = capture_resolution(
        incident_source="ci",
        incident_action="escalate_with_diagnosis",
        heal_capability="retrigger_workflow",
        root_cause="Flaky test",
        resolution_text="Retrigger the run",
        should_auto_heal=True,
    )
    assert cp.name.startswith("learned_")
    assert cp.heal_capability == "retrigger_workflow"
    assert cp.confidence == 0.5
    assert cp.success_count == 0


def test_capture_idempotent():
    _cleanup()
    cp1 = capture_resolution("ci", "esc", "retrigger_workflow", "flaky", "retrigger", True)
    cp2 = capture_resolution("ci", "esc", "retrigger_workflow", "updated", "retrigger v2", True)
    assert cp1.name == cp2.name
    assert cp2.diagnosis == "updated"


def test_find_matching_candidate():
    _cleanup()
    capture_resolution("daemon", "escalate_to_operator", "restart_daemon", "stuck", "restart", True)
    match = find_matching_candidate("daemon", "escalate_to_operator")
    assert match is not None
    assert match.heal_capability == "restart_daemon"


def test_no_match_for_different_source():
    _cleanup()
    capture_resolution("daemon", "escalate_to_operator", "restart_daemon", "stuck", "restart", True)
    match = find_matching_candidate("ci", "escalate_to_operator")
    assert match is None


def test_approve_increments():
    _cleanup()
    cp = capture_resolution("test", "esc", "restart_daemon", "root", "fix", True)
    approve_candidate(cp.name)
    assert cp.success_count == 1
    assert cp.confidence > 0.5


def test_reject_decrements():
    _cleanup()
    cp = capture_resolution("test", "esc", "restart_daemon", "root", "fix", True)
    reject_candidate(cp.name, "didn't work")
    assert cp.failure_count == 1
    assert cp.confidence < 0.5


def test_graduation_after_threshold():
    _cleanup()
    cp = capture_resolution("test", "esc", "restart_daemon", "root", "fix", True)
    for _ in range(GRADUATION_THRESHOLD):
        approve_candidate(cp.name)
    assert cp.graduated is True
    assert cp.confidence >= 0.85


def test_low_confidence_not_matched():
    _cleanup()
    cp = capture_resolution("test", "esc", "cap", "root", "fix", False)
    cp.confidence = 0.3
    match = find_matching_candidate("test", "esc")
    assert match is None
