"""Tests for the Tenant Deep Dive aggregator."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from nexus import tenant_deep_dive as tdd  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)
TID = "forge-test-tenant"


def setup_function(_fn):
    tdd.clear_cache()


def test_get_tenant_dive_returns_full_shape():
    r = tdd.get_tenant_dive(TID)
    assert r["tenant_id"] == TID
    assert "generated_at" in r
    for key in ("activity_timeline", "engagement", "pipeline",
                "intelligence_depth", "risk_signals", "recommendations"):
        assert key in r


def test_engagement_shape():
    r = tdd.get_tenant_dive(TID)
    e = r["engagement"]
    for key in ("last_active", "activity_trend", "engagement_score",
                "session_pattern", "messages_last_7d", "conversation_sentiment"):
        assert key in e
    assert 0 <= e["engagement_score"] <= 100
    assert e["activity_trend"] in ("rising", "stable", "falling")


def test_pipeline_shape():
    r = tdd.get_tenant_dive(TID)
    p = r["pipeline"]
    for key in ("tasks_total", "tasks_pending", "tasks_complete",
                "prs_open", "prs_merged", "pr_velocity_per_day",
                "deploy_status", "brief_freshness"):
        assert key in p
    assert p["tasks_total"] >= 0
    assert p["prs_open"] >= 0


def test_intelligence_depth_shape():
    r = tdd.get_tenant_dive(TID)
    i = r["intelligence_depth"]
    assert i["sources_total"] == 12
    assert i["sources_populated"] <= i["sources_total"]
    assert isinstance(i["populated_labels"], list)
    assert isinstance(i["analysis_reports"], list)


def test_recommendations_always_present():
    r = tdd.get_tenant_dive(TID)
    assert isinstance(r["recommendations"], list)
    assert len(r["recommendations"]) >= 1


def test_cache_hits_second_call():
    r1 = tdd.get_tenant_dive(TID)
    r2 = tdd.get_tenant_dive(TID)
    assert r1 is r2 or r1 == r2
    assert r1["generated_at"] == r2["generated_at"]


def test_force_bypasses_cache():
    r1 = tdd.get_tenant_dive(TID)
    time.sleep(0.01)
    r2 = tdd.get_tenant_dive(TID, force=True)
    assert r2["generated_at"] >= r1["generated_at"]


def test_clear_cache_single_tenant():
    tdd.get_tenant_dive(TID)
    assert TID in tdd._cache
    tdd.clear_cache(TID)
    assert TID not in tdd._cache


def test_engagement_rising_trend_with_synthetic_data(monkeypatch):
    """Inject fake conversation rows; verify trend detection."""
    now = datetime.now(timezone.utc)
    rows_user = [
        {"ts": (now - timedelta(days=1, hours=i)).isoformat(), "len": 50}
        for i in range(15)
    ] + [
        {"ts": (now - timedelta(days=10, hours=i)).isoformat(), "len": 50}
        for i in range(3)
    ]

    def fake_query(cypher, params=None):
        if "role: 'user'" in cypher:
            return rows_user
        return []

    monkeypatch.setattr(tdd.neptune_client, "query", fake_query)
    tdd.clear_cache()
    r = tdd.get_tenant_dive(TID)
    assert r["engagement"]["activity_trend"] == "rising"
    assert r["engagement"]["messages_last_7d"] == 15


def test_risk_signal_on_open_prs(monkeypatch):
    """Injected open PR count triggers pileup signal."""
    def fake_query(cypher, params=None):
        if "MissionTask" in cypher and "pr_state" in cypher:
            return [{"status": "in_review", "pr_state": "open"} for _ in range(4)]
        return []

    monkeypatch.setattr(tdd.neptune_client, "query", fake_query)
    tdd.clear_cache()
    r = tdd.get_tenant_dive(TID)
    assert r["pipeline"]["prs_open"] == 4
    signals = [s["signal"] for s in r["risk_signals"]]
    assert any("pileup" in s or "awaiting review" in s for s in signals)


def test_api_endpoint():
    resp = client.get(f"/api/tenant-dive/{TID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == TID
    assert "engagement" in body
    assert "pipeline" in body


def test_api_endpoint_force_param():
    resp = client.get(f"/api/tenant-dive/{TID}?force=true")
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == TID
