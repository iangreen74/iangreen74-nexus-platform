"""Tests for pattern promotion — the full self-programming loop."""
import json
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus import overwatch_graph  # noqa: E402
from nexus.reasoning.pattern_learner import (  # noqa: E402
    LEARNED_PATTERNS_FILE,
    CandidatePattern,
    _candidates,
    approve_candidate,
    capture_resolution,
    graduate_candidate,
    load_graduated_patterns,
)


def _cleanup():
    _candidates.clear()
    overwatch_graph.reset_local_store()
    if os.path.exists(LEARNED_PATTERNS_FILE):
        os.remove(LEARNED_PATTERNS_FILE)


def test_full_lifecycle():
    """The complete loop: capture → approve 3x → graduate → persist."""
    _cleanup()
    cp = capture_resolution("daemon", "escalate", "restart_daemon", "stuck hook", "restart fixes it", True)
    assert not cp.graduated
    for _ in range(3):
        approve_candidate(cp.name)
    assert cp.graduated
    assert cp.confidence >= 0.85
    _cleanup()


def test_graduated_pattern_persists():
    """Graduated patterns survive in the JSON file."""
    _cleanup()
    cp = capture_resolution("test", "esc", "restart_daemon", "cause", "fix", True)
    cp.success_count = 2
    approve_candidate(cp.name)  # 3rd triggers graduation
    loaded = load_graduated_patterns()
    assert len(loaded) >= 1
    assert loaded[0].graduated is True
    assert loaded[0].heal_capability == "restart_daemon"
    _cleanup()


def test_graduated_confidence_floor():
    _cleanup()
    cp = capture_resolution("test", "esc", "cap", "cause", "fix", True)
    cp.confidence = 0.5
    graduate_candidate(cp.name)
    assert cp.confidence >= 0.85
    _cleanup()


def test_candidate_to_dict_roundtrip():
    cp = CandidatePattern(
        name="test",
        signature="a:b",
        match_source="a",
        match_action="b",
        heal_capability="restart_daemon",
        diagnosis="root",
        resolution="fix",
    )
    d = cp.to_dict()
    cp2 = CandidatePattern.from_dict(d)
    assert cp2.name == cp.name
    assert cp2.heal_capability == cp.heal_capability
