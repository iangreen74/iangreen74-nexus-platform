"""Tests for nexus.capabilities.ci_healer."""
from __future__ import annotations

import os
from collections import deque
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import ci_healer as healer  # noqa: E402
from nexus.capabilities.registry import registry  # noqa: E402


def _clear_history():
    healer._kill_history.clear()


def test_registered():
    assert "heal_hung_ci" in {c.name for c in registry.list_all()}


def test_no_runner_name_refuses():
    _clear_history()
    assert healer.heal_hung_ci({})["reason"] == "no_runner_name"


def test_local_mode_mock_success():
    _clear_history()
    result = healer.heal_hung_ci({"runner_name": "aria-runner-3"})
    assert result["ok"] is True
    assert result["mock"] is True


def test_circuit_breaker_open_after_three():
    _clear_history()
    now = datetime.now(timezone.utc)
    healer._kill_history["aria-runner-3"] = deque([now, now, now])
    result = healer.heal_hung_ci({"runner_name": "aria-runner-3"})
    assert result["ok"] is False
    assert result["reason"] == "circuit_breaker_open"


def test_parse_worker_picks_first_nonmatching_grep():
    ps = (
        "  1 0.0 100 bash -bash\n"
        "  2 0.5 420 Runner.Worker /opt/actions-runner/bin/Runner.Worker\n"
        "  3 0.0 99 grep Runner.Worker\n"
    )
    w = healer._parse_worker(ps)
    assert w is not None
    assert w["pid"] == 2
    assert w["elapsed_sec"] == 420
    assert w["cpu_pct"] == 0.5


def test_parse_worker_none_when_absent():
    assert healer._parse_worker("no workers here") is None


def test_production_kill_flow_records_healing_action():
    """Happy path: instance found, worker wedged, kill commanded, action recorded."""
    _clear_history()
    sent_cmds: list[str] = []

    def fake_run_ssm(instance_id, command, timeout_sec=30):
        sent_cmds.append(command)
        if "ps -eo" in command:
            return (
                "  7 0.0 600 Runner.Worker /opt/actions-runner/bin/Runner.Worker Idle\n"
            )
        return ""

    recorded: list[dict] = []
    def fake_healing(action_type, target, blast_radius, trigger, outcome, duration_ms=None):
        recorded.append({"action_type": action_type, "target": target,
                          "outcome": outcome})
        return "ha-1"

    with patch("nexus.capabilities.ci_healer.MODE", "production"), \
         patch("nexus.capabilities.ci_healer._find_instance_id",
               return_value="i-abc"), \
         patch("nexus.capabilities.ci_healer._run_ssm", side_effect=fake_run_ssm), \
         patch("nexus.capabilities.ci_healer.overwatch_graph.record_healing_action",
               side_effect=fake_healing):
        out = healer.heal_hung_ci({
            "runner_name": "aria-runner-3",
            "incident_id": "inc-1",
        })

    assert out["ok"] is True
    assert out["killed_pid"] == 7
    assert len(recorded) == 1
    assert recorded[0]["action_type"] == "ci_kill_hung_worker"
    # Two SSM calls: ps, then kill
    assert any("kill -TERM 7" in c for c in sent_cmds)


def test_production_no_instance_means_no_kill():
    _clear_history()
    with patch("nexus.capabilities.ci_healer.MODE", "production"), \
         patch("nexus.capabilities.ci_healer._find_instance_id", return_value=None):
        out = healer.heal_hung_ci({"runner_name": "aria-runner-x"})
    assert out["ok"] is False
    assert out["reason"] == "instance_not_found"


def test_production_worker_not_wedged_means_no_kill():
    _clear_history()
    def fake_run_ssm(instance_id, command, timeout_sec=30):
        # Worker is busy (50% CPU) and only 60s old — not wedged.
        return "  7 50.0 60 Runner.Worker\n"

    with patch("nexus.capabilities.ci_healer.MODE", "production"), \
         patch("nexus.capabilities.ci_healer._find_instance_id", return_value="i-abc"), \
         patch("nexus.capabilities.ci_healer._run_ssm", side_effect=fake_run_ssm):
        out = healer.heal_hung_ci({"runner_name": "aria-runner-3"})
    assert out["ok"] is False
    assert out["reason"] == "worker_not_wedged"
