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
