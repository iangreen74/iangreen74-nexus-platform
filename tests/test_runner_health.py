"""Tests for runner health monitoring + auto-fix."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock, patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from nexus.runner_health import (  # noqa: E402
    DISK_THRESHOLD,
    RUNNER_NAMES,
    _get_runner_instances,
    _parse,
    check_all_runners,
    format_for_report,
    get_summary,
)
from nexus.server import app  # noqa: E402

client = TestClient(app)


def _clear_cache():
    import nexus.runner_health as rh
    rh._cache = ([], 0)


# --- _parse -----------------------------------------------------------------


def test_parse_extracts_value():
    output = "DISK:42\nDOCKER:27.5.1\nAGENT:active\nSOCKET:666"
    assert _parse(output, "DISK:") == "42"
    assert _parse(output, "DOCKER:") == "27.5.1"
    assert _parse(output, "AGENT:") == "active"
    assert _parse(output, "SOCKET:") == "666"


def test_parse_missing_prefix():
    assert _parse("FOO:bar", "DISK:") is None


def test_parse_empty_output():
    assert _parse("", "DISK:") is None


def test_parse_handles_whitespace():
    output = "  DISK:   42  "
    assert _parse(output, "DISK:") == "42"


# --- check_all_runners (local mode) ------------------------------------------


def test_check_all_runners_local_mode():
    _clear_cache()
    results = check_all_runners(force=True)
    assert len(results) == len(RUNNER_NAMES)
    for r in results:
        assert r["runner"] in RUNNER_NAMES
        assert r["healthy"] is True
        assert r["actions"] == []
        assert r.get("mock") is True


def test_check_all_runners_cached():
    _clear_cache()
    r1 = check_all_runners(force=True)
    r2 = check_all_runners()
    assert r1 == r2


def test_force_bypasses_cache():
    _clear_cache()
    check_all_runners(force=True)
    import nexus.runner_health as rh
    rh._cache = ([{"runner": "fake"}], rh._cache[1])
    results = check_all_runners(force=True)
    assert len(results) == len(RUNNER_NAMES)


# --- Auto-fix dispatch with mocked SSM ---------------------------------------


def _make_ssm_mock(disk: str, docker: str, agent: str, socket: str) -> MagicMock:
    """Build a mock SSM client returning the given runner state."""
    ssm = MagicMock()
    ssm.send_command.return_value = {"Command": {"CommandId": "cmd-1"}}
    output = f"DISK:{disk}\nDOCKER:{docker}\nAGENT:{agent}\nSOCKET:{socket}"
    ssm.get_command_invocation.return_value = {"StandardOutputContent": output}
    return ssm


def test_disk_over_threshold_triggers_prune():
    from nexus.runner_health import _check_runner

    ssm = _make_ssm_mock("95", "27.5.1", "active", "666")
    with patch("time.sleep"):
        result = _check_runner(ssm, "i-abc", "aria-runner-3")
    assert result["disk_pct"] == "95"
    assert any("pruned" in a for a in result["actions"])
    assert result["healthy"] is False


def test_docker_down_triggers_restart():
    from nexus.runner_health import _check_runner

    ssm = _make_ssm_mock("42", "DOWN", "active", "666")
    with patch("time.sleep"):
        result = _check_runner(ssm, "i-abc", "aria-runner-3")
    assert any("restarted docker" in a for a in result["actions"])


def test_agent_inactive_triggers_restart():
    from nexus.runner_health import _check_runner

    ssm = _make_ssm_mock("42", "27.5.1", "failed", "666")
    with patch("time.sleep"):
        result = _check_runner(ssm, "i-abc", "aria-runner-3")
    assert any("restarted agent" in a for a in result["actions"])


def test_socket_perms_wrong_triggers_chmod():
    from nexus.runner_health import _check_runner

    ssm = _make_ssm_mock("42", "27.5.1", "active", "755")
    with patch("time.sleep"):
        result = _check_runner(ssm, "i-abc", "aria-runner-3")
    assert any("socket perms" in a for a in result["actions"])


def test_healthy_runner_no_actions():
    from nexus.runner_health import _check_runner

    ssm = _make_ssm_mock("42", "27.5.1", "active", "666")
    with patch("time.sleep"):
        result = _check_runner(ssm, "i-abc", "aria-runner-3")
    assert result["actions"] == []
    assert result["healthy"] is True


def test_multiple_issues_all_fixed():
    """Disk full + docker down + bad socket → 3 actions."""
    from nexus.runner_health import _check_runner

    ssm = _make_ssm_mock("95", "DOWN", "active", "755")
    with patch("time.sleep"):
        result = _check_runner(ssm, "i-abc", "aria-runner-3")
    assert len(result["actions"]) == 3


def test_ssm_failure_returns_error():
    from nexus.runner_health import _check_runner

    ssm = MagicMock()
    ssm.send_command.side_effect = Exception("SSM timeout")
    result = _check_runner(ssm, "i-abc", "aria-runner-3")
    assert "error" in result
    assert result["healthy"] is False


# --- _get_runner_instances ---------------------------------------------------


def test_get_runner_instances_graceful_on_error():
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = Exception("AWS down")
    result = _get_runner_instances(ec2)
    assert result == []


def test_get_runner_instances_parses_tags():
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [{
                "InstanceId": "i-abc",
                "Tags": [{"Key": "Name", "Value": "aria-runner-3"}],
            }],
        }],
    }
    result = _get_runner_instances(ec2)
    assert result == [("i-abc", "aria-runner-3")]


# --- format_for_report -------------------------------------------------------


def test_format_empty():
    assert "no data" in format_for_report([])


def test_format_healthy_runner():
    results = [{"runner": "aria-runner-3", "disk_pct": "42",
                "docker": "27.5.1", "agent": "active",
                "actions": [], "healthy": True}]
    text = format_for_report(results)
    assert "RUNNER HEALTH" in text
    assert "aria-runner-3" in text
    assert "healthy" in text
    assert "42%" in text


def test_format_fixed_runner():
    results = [{"runner": "aria-runner-3", "disk_pct": "95",
                "docker": "27.5.1", "agent": "active",
                "actions": ["pruned (disk was 95%)"], "healthy": False}]
    text = format_for_report(results)
    assert "fixed" in text
    assert "pruned" in text


def test_format_error_runner():
    results = [{"runner": "aria-runner-3", "error": "SSM timeout", "healthy": False}]
    text = format_for_report(results)
    assert "ERROR" in text
    assert "SSM timeout" in text


# --- get_summary -------------------------------------------------------------


def test_summary_structure():
    _clear_cache()
    summary = get_summary()
    assert "total" in summary
    assert "healthy" in summary
    assert "fixed" in summary
    assert "errors" in summary
    assert "runners" in summary


# --- Endpoints ---------------------------------------------------------------


def test_runners_endpoint():
    resp = client.get("/api/runners")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "runners" in body


def test_runners_check_endpoint():
    resp = client.post("/api/runners/check")
    assert resp.status_code == 200
    body = resp.json()
    assert "runners" in body


def test_report_has_runner_section():
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert "RUNNER HEALTH" in report
