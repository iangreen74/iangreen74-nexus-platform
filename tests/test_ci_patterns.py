"""Tests for nexus.capabilities.ci_patterns."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

os.environ.setdefault("NEXUS_MODE", "local")

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import ci_patterns as cp  # noqa: E402
from nexus.capabilities.registry import registry  # noqa: E402


def _reset_graph():
    overwatch_graph.reset_local_store()


def _seed_incident(job: str, step: str, runner: str = "aria-runner-3",
                    repo: str = "aria-platform", commit: str = "abc123") -> None:
    overwatch_graph.record_event(
        event_type="ci_hung",
        service=f"github-actions:{repo}",
        details={"job_name": job, "current_step": step, "runner_name": runner,
                 "repo": repo, "commit": commit},
        severity="warning",
    )


def test_registered():
    assert "learn_ci_patterns" in {c.name for c in registry.list_all()}


def test_no_incidents_no_antipatterns():
    _reset_graph()
    out = cp.learn_ci_patterns()
    assert out["antipattern_count"] == 0
    assert out["antipatterns"] == []


def test_below_threshold_no_antipattern():
    _reset_graph()
    _seed_incident("e2e-tests", "Install Playwright")
    _seed_incident("e2e-tests", "Install Playwright")
    out = cp.learn_ci_patterns()
    assert out["antipattern_count"] == 0


def test_three_same_pair_becomes_antipattern():
    _reset_graph()
    for _ in range(3):
        _seed_incident("e2e-tests", "Install Playwright")
    out = cp.learn_ci_patterns()
    assert out["antipattern_count"] == 1
    ap = out["antipatterns"][0]
    assert ap["job"] == "e2e-tests"
    assert ap["step"] == "Install Playwright"
    assert ap["count"] == 3
    # Brief names the hint for apt-get-like installs
    assert "dpkg" in ap["brief"] or "apt-get" in ap["brief"] or "Playwright" in ap["brief"]
    # FailurePattern + ActionRequired were both upserted
    patterns = overwatch_graph._local_store["OverwatchFailurePattern"]
    assert any(p["name"].startswith("ci_hang:") for p in patterns)
    actions = overwatch_graph._local_store["ActionRequired"]
    assert any(a["tenant_id"] == "__platform__" for a in actions)


def test_different_pairs_counted_separately():
    _reset_graph()
    for _ in range(3):
        _seed_incident("e2e-tests", "Install Playwright")
    for _ in range(2):
        _seed_incident("test", "npm install")
    out = cp.learn_ci_patterns()
    # Only the 3-count pair becomes an antipattern
    assert out["antipattern_count"] == 1
    assert out["antipatterns"][0]["job"] == "e2e-tests"


def test_decode_details_accepts_dict_and_json_string():
    assert cp._decode_details({"details": {"a": 1}}) == {"a": 1}
    assert cp._decode_details({"details": json.dumps({"b": 2})}) == {"b": 2}
    assert cp._decode_details({"details": ""}) == {}
    assert cp._decode_details({}) == {}


def test_step_hint_matches_playwright():
    assert "dpkg" in cp._hint_for_step("Install Playwright browsers")
    assert cp._hint_for_step("Run tests") == ""
