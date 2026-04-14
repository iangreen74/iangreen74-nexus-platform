"""Tests for nexus.capabilities.ci_heartbeat."""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import ci_heartbeat as hb  # noqa: E402
from nexus.capabilities.registry import registry  # noqa: E402


def test_registered():
    assert "check_ci_heartbeat" in {c.name for c in registry.list_all()}


def test_local_mode_is_mock():
    result = hb.check_ci_heartbeat()
    assert result["mock"] is True
    assert result["hung"] == []


def test_budget_for_known_and_unknown():
    assert hb._budget_for("e2e-tests (ubuntu-latest)") == 480
    assert hb._budget_for("test-staging / frontend") == 480
    assert hb._budget_for("completely-unknown-job") == hb._DEFAULT_BUDGET_SEC


def test_elapsed_sec_handles_bad_input():
    assert hb._elapsed_sec(None) == 0
    assert hb._elapsed_sec("garbage") == 0
    assert hb._elapsed_sec("2020-01-01T00:00:00Z") > 0


def test_current_step_picks_in_progress():
    job = {"steps": [
        {"name": "Checkout", "status": "completed"},
        {"name": "Install Playwright", "status": "in_progress"},
        {"name": "Run tests", "status": "queued"},
    ]}
    assert hb._current_step(job) == "Install Playwright"


def test_production_hang_detection_records_incident():
    """With a mocked GitHub API returning a hung e2e-tests job, a CIIncident must be recorded."""
    runs_payload = {"workflow_runs": [{
        "id": 123, "head_sha": "abcdef123456",
        "html_url": "https://x/runs/123",
    }]}
    # Started 30 minutes ago — well past the e2e-tests 480s budget.
    jobs_payload = {"jobs": [{
        "id": 9, "name": "e2e-tests / chromium",
        "status": "in_progress",
        "started_at": "2020-01-01T00:00:00Z",
        "runner_name": "aria-runner-3",
        "steps": [{"name": "Install Playwright", "status": "in_progress"}],
    }]}

    class FakeResp:
        def __init__(self, data): self._data = data; self.status_code = 200; self.text = ""
        def json(self): return self._data

    call_seq = [FakeResp(runs_payload), FakeResp(jobs_payload)]
    def fake_get(url, **kwargs):
        return call_seq.pop(0) if call_seq else FakeResp({})

    recorded: list[dict] = []
    def fake_record(event_type, service, details, severity):
        recorded.append({"event_type": event_type, "service": service,
                          "details": details, "severity": severity})
        return "evt-1"

    with patch("nexus.capabilities.ci_heartbeat.MODE", "production"), \
         patch("nexus.capabilities.ci_heartbeat._token", return_value="tok"), \
         patch("nexus.capabilities.ci_heartbeat.GITHUB_REPOS", ["aria-platform"]), \
         patch("nexus.capabilities.ci_heartbeat.httpx.get", side_effect=fake_get), \
         patch("nexus.capabilities.ci_heartbeat.overwatch_graph.record_event",
               side_effect=fake_record):
        result = hb.check_ci_heartbeat()

    assert result["hung_count"] == 1
    assert result["hung"][0]["runner_name"] == "aria-runner-3"
    assert result["hung"][0]["current_step"] == "Install Playwright"
    assert len(recorded) == 1
    assert recorded[0]["event_type"] == "ci_hung"
    assert recorded[0]["details"]["runner_name"] == "aria-runner-3"


def test_missing_token_returns_error():
    with patch("nexus.capabilities.ci_heartbeat.MODE", "production"), \
         patch("nexus.capabilities.ci_heartbeat._token", return_value=None):
        result = hb.check_ci_heartbeat()
    assert result["error"] == "missing_github_token"
    assert result["hung"] == []
