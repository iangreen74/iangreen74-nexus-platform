"""
Tests for the preemptive health sensor.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.sensors import preemptive  # noqa: E402


def test_run_preemptive_returns_list():
    alerts = preemptive.run_preemptive_checks()
    assert isinstance(alerts, list)


def test_unwired_checks_are_marked_honestly():
    """Stubbed checks must declare they're not wired, not fake green."""
    alerts = preemptive.run_preemptive_checks()
    unwired = [a for a in alerts if a.get("status") == "unknown_needs_wiring"]
    names = {a["check_name"] for a in unwired}
    assert "github_token_freshness" in names
    assert "neptune_storage" in names
    assert "bedrock_throttling" in names


def test_alert_shape():
    alerts = preemptive.run_preemptive_checks()
    for alert in alerts:
        for key in ("check_name", "severity", "message", "suggested_action", "status"):
            assert key in alert, f"missing {key} in {alert}"
        assert alert["severity"] in ("info", "warning", "critical")


def test_real_checks_skipped_in_local_mode():
    """ECS task age and ACM cert expiry should produce zero alerts in local."""
    age = preemptive.check_ecs_task_age()
    certs = preemptive.check_certificate_expiry()
    assert age == []
    assert certs == []


def test_secret_expiry_respects_known_expiries(monkeypatch):
    """If KNOWN_SECRET_EXPIRIES has a soon-to-expire secret, it should fire."""
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    monkeypatch.setattr(preemptive, "KNOWN_SECRET_EXPIRIES", {"github-token": soon})
    alerts = preemptive.check_known_secret_expiries()
    assert len(alerts) == 1
    assert alerts[0]["check_name"] == "secret_expiry"
    assert alerts[0]["severity"] in ("warning", "critical")


def test_secret_expiry_silent_when_far_future(monkeypatch):
    monkeypatch.setattr(preemptive, "KNOWN_SECRET_EXPIRIES", {"github-token": "2099-01-01"})
    assert preemptive.check_known_secret_expiries() == []
