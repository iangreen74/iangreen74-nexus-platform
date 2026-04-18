"""Scaffold verification — imports, shapes, stubs behave as documented."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest

from nexus.intelligence.training import dataset, model, train
from nexus.intelligence.training.dataset import TrainingExample, corpus_stats
from nexus.intelligence.training.model import (
    EMBEDDING_DIM,
    BaselineHeuristicModel,
    QwenFingerprintModel,
)


def test_imports():
    assert dataset is not None
    assert model is not None
    assert train is not None


def test_load_returns_list():
    examples = dataset.load_training_examples()
    assert isinstance(examples, list)


def test_corpus_stats_empty():
    stats = corpus_stats([])
    assert stats["total"] == 0
    assert stats["pat_types"] == []


def test_corpus_stats_populated():
    ex = [
        TrainingExample(
            attempt_id=f"a{i}", tenant_id="t1", project_id="p1",
            pat_type="node/express" if i % 2 else "python/flask",
            repo_full="r1", fingerprint="f1",
            deploy_success=i < 3, correction_count=i,
            template_quality_score=0.5, error_message="",
            started_at=f"2026-04-{10+i:02d}T00:00:00Z",
            ended_at=f"2026-04-{10+i:02d}T01:00:00Z",
        )
        for i in range(5)
    ]
    stats = corpus_stats(ex)
    assert stats["total"] == 5
    assert stats["success"] == 3
    assert stats["failure"] == 2
    assert "node/express" in stats["pat_types"]
    assert "python/flask" in stats["pat_types"]


def test_baseline_embed_shape():
    m = BaselineHeuristicModel()
    vec = m.embed("python/flask", "iangreen74/my-app")
    assert isinstance(vec, list)
    assert len(vec) == EMBEDDING_DIM
    assert all(0.0 <= v <= 1.0 for v in vec)


def test_baseline_embed_deterministic():
    m = BaselineHeuristicModel()
    v1 = m.embed("python/flask", "repo-a")
    v2 = m.embed("python/flask", "repo-a")
    assert v1 == v2


def test_baseline_embed_differs_for_different_inputs():
    m = BaselineHeuristicModel()
    v1 = m.embed("python/flask", "repo-a")
    v2 = m.embed("node/express", "repo-b")
    assert v1 != v2


def test_baseline_train_is_noop():
    m = BaselineHeuristicModel()
    result = m.train([])
    assert result["status"] == "no_training_needed"


def test_qwen_train_raises():
    m = QwenFingerprintModel()
    with pytest.raises(NotImplementedError, match="Sprint 15"):
        m.train([])


def test_qwen_predict_raises():
    m = QwenFingerprintModel()
    with pytest.raises(NotImplementedError, match="checkpoint"):
        m.predict_outcome("python/flask", "repo")


def test_qwen_embed_raises():
    m = QwenFingerprintModel()
    with pytest.raises(NotImplementedError, match="checkpoint"):
        m.embed("python/flask", "repo")


def test_run_training_no_data():
    result = train.run_training(model_name="baseline")
    assert result["status"] == "no_data"
    assert result["corpus"]["total"] == 0


def test_run_training_unknown_model():
    # With no data in local mode, early-exits before model lookup.
    # When data exists, unknown model would return "error".
    result = train.run_training(model_name="nonexistent")
    assert result["status"] in ("no_data", "error")
