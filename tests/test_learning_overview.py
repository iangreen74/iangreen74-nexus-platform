"""Tests for the Learning tab aggregator."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus import learning_overview as lo  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


def setup_function(_fn):
    lo.clear_cache()


# --- Shape ------------------------------------------------------------------


def test_overview_returns_full_shape():
    data = lo.get_overview(force=True)
    for key in ("training", "dogfood", "patterns", "recent_runs",
                "model", "generated_at"):
        assert key in data


def test_training_shape():
    t = lo.get_overview(force=True)["training"]
    for key in ("total_examples", "avg_quality", "coverage_count",
                "fingerprints", "progress_pct", "examples_needed",
                "threshold"):
        assert key in t
    assert t["threshold"] == 1000
    assert t["total_examples"] >= 0
    assert 0 <= t["progress_pct"] <= 100


def test_dogfood_shape():
    d = lo.get_overview(force=True)["dogfood"]
    for key in ("total_runs", "successes", "failures", "timeouts",
                "success_rate", "runs_today", "cost_today_usd",
                "enabled", "circuit_open"):
        assert key in d
    assert isinstance(d["enabled"], bool)
    assert isinstance(d["circuit_open"], bool)


def test_patterns_and_recent_runs_are_lists():
    data = lo.get_overview(force=True)
    assert isinstance(data["patterns"], list)
    assert isinstance(data["recent_runs"], list)


def test_model_state_not_started_below_threshold():
    data = lo.get_overview(force=True)
    assert data["model"]["status"] == "not_started"
    assert data["model"]["finetuning_runs"] == 0


# --- Business logic ---------------------------------------------------------


def test_ready_to_bypass_sonnet_requires_uses_and_quality(monkeypatch):
    def fake_query(cypher, params=None):
        if "WHERE d.deploy_success = true" in cypher and "WITH d.fingerprint" in cypher:
            return [
                {"fp": "python/flask", "uses": 6, "avg_q": 0.95},
                {"fp": "node/express", "uses": 5, "avg_q": 0.85},
                {"fp": "go/gin", "uses": 3, "avg_q": 0.99},
            ]
        return []
    monkeypatch.setattr(lo.neptune_client, "query", fake_query)
    lo.clear_cache()
    patterns = lo.get_overview(force=True)["patterns"]
    by_fp = {p["fingerprint"]: p for p in patterns}
    assert by_fp["python/flask"]["ready_to_bypass_sonnet"] is True
    # Quality too low → not ready
    assert by_fp["node/express"]["ready_to_bypass_sonnet"] is False
    # Uses too low → not ready
    assert by_fp["go/gin"]["ready_to_bypass_sonnet"] is False


def test_circuit_open_when_majority_fail(monkeypatch):
    def fake_query(cypher, params=None):
        if "MATCH (d:DogfoodRun)" in cypher and "total_runs" in cypher:
            return [{"total_runs": 20, "successes": 5, "failures": 13,
                     "timeouts": 2}]
        return []
    monkeypatch.setattr(lo.neptune_client, "query", fake_query)
    lo.clear_cache()
    d = lo.get_overview(force=True)["dogfood"]
    assert d["circuit_open"] is True


def test_circuit_closed_when_below_sample_threshold(monkeypatch):
    def fake_query(cypher, params=None):
        if "MATCH (d:DogfoodRun)" in cypher and "total_runs" in cypher:
            return [{"total_runs": 5, "successes": 0, "failures": 5,
                     "timeouts": 0}]
        return []
    monkeypatch.setattr(lo.neptune_client, "query", fake_query)
    lo.clear_cache()
    d = lo.get_overview(force=True)["dogfood"]
    assert d["circuit_open"] is False


def test_training_total_and_threshold(monkeypatch):
    def fake_query(cypher, params=None):
        if "RETURN count(d) AS total_examples" in cypher:
            return [{"total_examples": 250}]
        if "RETURN avg(toFloat" in cypher:
            return [{"avg_quality": 0.87}]
        if "RETURN DISTINCT d.fingerprint" in cypher:
            return [{"fp": "python/flask"}, {"fp": "node/express"}]
        return []
    monkeypatch.setattr(lo.neptune_client, "query", fake_query)
    lo.clear_cache()
    t = lo.get_overview(force=True)["training"]
    assert t["total_examples"] == 250
    assert t["coverage_count"] == 2
    assert t["progress_pct"] == 25
    assert t["examples_needed"] == 750
    assert t["avg_quality"] == 0.87


# --- Fine-tuning gate -------------------------------------------------------


def test_trigger_finetuning_below_threshold_returns_error():
    lo.clear_cache()
    result = lo.trigger_finetuning()
    assert "error" in result
    assert result["ready"] is False


def test_trigger_finetuning_above_threshold_queues(monkeypatch):
    def fake_query(cypher, params=None):
        if "RETURN count(d) AS total_examples" in cypher:
            return [{"total_examples": 1500}]
        if "RETURN DISTINCT d.fingerprint" in cypher:
            return [{"fp": "python/flask"}]
        return []
    monkeypatch.setattr(lo.neptune_client, "query", fake_query)
    lo.clear_cache()
    result = lo.trigger_finetuning()
    assert result.get("status") == "queued"
    assert result.get("examples") == 1500


# --- API endpoints ----------------------------------------------------------


def test_learning_overview_endpoint():
    resp = client.get("/api/learning-overview")
    assert resp.status_code == 200
    body = resp.json()
    assert "training" in body and "dogfood" in body


def test_trigger_finetuning_endpoint_rejects_below_threshold():
    lo.clear_cache()
    resp = client.post("/api/trigger-finetuning")
    assert resp.status_code == 409


def test_trigger_finetuning_endpoint_queues_when_ready(monkeypatch):
    def fake_query(cypher, params=None):
        if "RETURN count(d) AS total_examples" in cypher:
            return [{"total_examples": 1500}]
        return []
    monkeypatch.setattr(lo.neptune_client, "query", fake_query)
    lo.clear_cache()
    resp = client.post("/api/trigger-finetuning")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


def test_cache_hits_on_repeat_call():
    r1 = lo.get_overview(force=True)
    r2 = lo.get_overview()
    assert r1["generated_at"] == r2["generated_at"]


# --- Batch runs -------------------------------------------------------------


def test_run_batch_invalid_count():
    r = lo.run_batch(42)
    assert "error" in r


def test_run_batch_valid_count():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    r = lo.run_batch(100)
    assert r.get("ok") is True
    assert r["count"] == 100
    assert r["estimated_cost_usd"] == 15.0
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)


def test_run_batch_rejects_when_active():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    lo.run_batch(100)
    r = lo.run_batch(200)
    assert "error" in r
    assert "already running" in r["error"]
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)


def test_batch_status_no_active():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    r = lo.batch_status()
    assert r["active"] is False


def test_batch_status_with_active():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    lo.run_batch(200)
    r = lo.batch_status()
    assert r["active"] is True
    assert r["requested"] == 200
    assert r["remaining"] == 200
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)


def test_batch_status_endpoint():
    resp = client.get("/api/dogfood/batch-status")
    assert resp.status_code == 200
    assert "active" in resp.json()


def test_run_batch_endpoint():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    resp = client.post("/api/dogfood/run-batch", json={"count": 100})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)


def test_run_batch_endpoint_bad_count():
    resp = client.post("/api/dogfood/run-batch", json={"count": 42})
    assert resp.status_code == 400


# --- CI/CD metrics ----------------------------------------------------------


def test_cicd_metrics_shape():
    m = lo.cicd_metrics()
    for key in ("total_deploys", "success_rate_7d",
                "avg_time_to_healthy_seconds", "bypass_rate_7d",
                "active_failures", "recent_failures"):
        assert key in m
    assert isinstance(m["recent_failures"], list)


def test_cicd_metrics_endpoint():
    resp = client.get("/api/cicd-metrics")
    assert resp.status_code == 200
    assert "success_rate_7d" in resp.json()


# --- Intelligence score -----------------------------------------------------


def test_intelligence_score_shape():
    s = lo.intelligence_score()
    assert "current_score" in s
    assert "score_breakdown" in s
    assert s["score_breakdown"]["base"] == 60
    assert 60 <= s["current_score"] <= 100


def test_intelligence_score_endpoint():
    resp = client.get("/api/intelligence-score")
    assert resp.status_code == 200
    body = resp.json()
    assert "current_score" in body
    assert "score_history" in body


# --- Neptune activation + schedule ------------------------------------------


def test_run_batch_stores_tenant_id_in_config():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)
    lo.run_batch(100)
    config = overwatch_graph.get_dogfood_config()
    assert config.get("tenant_id") == lo.DOGFOOD_TENANT_ID
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)


def test_dogfood_cycle_reads_tenant_from_config():
    from nexus import overwatch_graph
    from nexus.capabilities.dogfood_capability import run_dogfood_cycle
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)
    overwatch_graph.set_dogfood_config(enabled=True, activated_by="test",
                                       tenant_id="forge-test-tenant")
    result = run_dogfood_cycle()
    assert result.get("skipped") is not True or result.get("reason") != "no tenant_id"
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)


def test_run_batch_activates_neptune_config():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)
    lo.run_batch(100)
    config = overwatch_graph.get_dogfood_config()
    assert config.get("enabled") is True
    assert config.get("activated_by") == "batch"
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)


def test_resolve_enabled_reads_neptune_first():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)
    overwatch_graph.set_dogfood_config(enabled=True, activated_by="test")
    enabled, source = lo._resolve_enabled()
    assert enabled is True
    assert source == "test"
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)


def test_resolve_enabled_falls_back_to_env(monkeypatch):
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)
    monkeypatch.setenv("DOGFOOD_ENABLED", "true")
    enabled, source = lo._resolve_enabled()
    assert enabled is True
    assert source == "env"


def test_schedule_get_empty():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodSchedule", None)
    s = lo.get_schedule()
    assert s["runs_per_day"] == 0
    assert s["enabled"] is False


def test_schedule_set_and_get():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodSchedule", None)
    result = lo.set_schedule(50)
    assert result["ok"] is True
    assert result["cost_per_day_usd"] == 7.5
    s = lo.get_schedule()
    assert s["runs_per_day"] == 50
    assert s["enabled"] is True
    overwatch_graph._local_store.pop("OverwatchDogfoodSchedule", None)


def test_schedule_invalid_rpd():
    result = lo.set_schedule(42)
    assert "error" in result


def test_cancel_batch_when_active():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)
    lo.run_batch(200)
    assert lo.batch_status()["active"] is True
    result = lo.cancel_batch()
    assert result["cancelled"] is True
    assert result["runs_cancelled"] == 200
    assert lo.batch_status()["active"] is False
    config = overwatch_graph.get_dogfood_config()
    assert config.get("enabled") is False
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)


def test_cancel_batch_when_none_active():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    result = lo.cancel_batch()
    assert result["cancelled"] is False


def test_cancel_batch_endpoint():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)
    lo.run_batch(100)
    resp = client.post("/api/dogfood/cancel-batch")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True
    overwatch_graph._local_store.pop("OverwatchDogfoodBatch", None)
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)


def test_schedule_endpoints():
    from nexus import overwatch_graph
    overwatch_graph._local_store.pop("OverwatchDogfoodSchedule", None)
    resp = client.get("/api/dogfood/schedule")
    assert resp.status_code == 200
    assert resp.json()["runs_per_day"] == 0

    resp = client.post("/api/dogfood/schedule", json={"runs_per_day": 10})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    overwatch_graph._local_store.pop("OverwatchDogfoodSchedule", None)


def test_dogfood_is_enabled_reads_neptune():
    from nexus import overwatch_graph
    from nexus.capabilities.dogfood_capability import _is_enabled
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)
    assert _is_enabled() is False  # no config, no env var
    overwatch_graph.set_dogfood_config(enabled=True, activated_by="test")
    assert _is_enabled() is True
    overwatch_graph._local_store.pop("OverwatchDogfoodConfig", None)


def test_intelligence_score_with_data(monkeypatch):
    def fake_query(cypher, params=None):
        if "RETURN count(d) AS total_examples" in cypher:
            return [{"total_examples": 500}]
        if "RETURN avg(toFloat" in cypher:
            return [{"avg_quality": 0.9}]
        if "RETURN DISTINCT d.fingerprint" in cypher:
            return [{"fp": "python/flask"}, {"fp": "node/express"}]
        if "WITH d.fingerprint" in cypher:
            return [
                {"fp": "python/flask", "uses": 6, "avg_q": 0.95},
                {"fp": "node/express", "uses": 5, "avg_q": 0.92},
            ]
        return []
    monkeypatch.setattr(lo.neptune_client, "query", fake_query)
    lo.clear_cache()
    s = lo.intelligence_score()
    assert s["current_score"] > 60
    assert s["score_breakdown"]["from_bypasses"] == 8
    assert s["score_breakdown"]["from_examples"] == 10
