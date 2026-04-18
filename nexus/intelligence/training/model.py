"""Model interfaces — abstract base + two concrete subclasses.

BaselineHeuristicModel: runs in production today. Uses Neptune RAG via
the pattern library for retrieval-based predictions. No ML, no GPU.

QwenFingerprintModel: the fine-tuned model (Qwen2.5-7B per VISION.md).
All ML methods raise NotImplementedError until Sprint 15 bake-off lands
a trained checkpoint.
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from nexus.intelligence.training.dataset import TrainingExample

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 32


@dataclass
class Prediction:
    """Model output for a single fingerprint."""
    success_probability: float
    similar_attempts: list[dict[str, Any]]
    confidence: float
    model_name: str


class FingerprintModel(ABC):
    """Base class for all fingerprint models."""

    @abstractmethod
    def train(self, examples: list[TrainingExample]) -> dict[str, Any]:
        """Train or fine-tune the model. Returns training metrics."""

    @abstractmethod
    def predict_outcome(self, pat_type: str, repo_full: str) -> Prediction:
        """Predict deploy success probability for a fingerprint."""

    @abstractmethod
    def embed(self, pat_type: str, repo_full: str) -> list[float]:
        """Produce a fixed-dimension embedding for a fingerprint."""

    @abstractmethod
    def save(self, path: str) -> None:
        """Persist model weights/state to disk."""

    @abstractmethod
    def load(self, path: str) -> None:
        """Load model weights/state from disk."""


class BaselineHeuristicModel(FingerprintModel):
    """Retrieval-based baseline — no ML, no GPU. Production-viable today.

    embed(): deterministic hash-based vector (for cosine similarity in
    the pattern library, not for gradient descent).

    predict_outcome(): queries Neptune for similar past DeployAttempts
    by pat_type match and returns their success rate as the probability.
    """

    def train(self, examples: list[TrainingExample]) -> dict[str, Any]:
        logger.info("BaselineHeuristicModel.train: no-op (retrieval-based)")
        return {"model": "baseline_heuristic", "status": "no_training_needed"}

    def predict_outcome(self, pat_type: str, repo_full: str) -> Prediction:
        from nexus import neptune_client
        rows = neptune_client.query(
            "MATCH (d:DeployAttempt {pat_type: $pat}) "
            "WHERE d.ended_at IS NOT NULL "
            "RETURN d.deploy_success AS s, d.attempt_id AS aid "
            "ORDER BY d.ended_at DESC LIMIT 20",
            {"pat": pat_type},
        ) or []
        if not rows:
            return Prediction(
                success_probability=0.5,
                similar_attempts=[],
                confidence=0.0,
                model_name="baseline_heuristic",
            )
        successes = sum(1 for r in rows if r.get("s"))
        return Prediction(
            success_probability=round(successes / len(rows), 3),
            similar_attempts=[{"attempt_id": r.get("aid")} for r in rows[:5]],
            confidence=min(0.9, len(rows) / 20),
            model_name="baseline_heuristic",
        )

    def embed(self, pat_type: str, repo_full: str) -> list[float]:
        raw = f"{pat_type}|{repo_full}".encode()
        digest = hashlib.sha256(raw).digest()
        vec = [b / 255.0 for b in digest[:EMBEDDING_DIM]]
        return vec

    def save(self, path: str) -> None:
        logger.info("BaselineHeuristicModel.save: no-op (stateless)")

    def load(self, path: str) -> None:
        logger.info("BaselineHeuristicModel.load: no-op (stateless)")


class QwenFingerprintModel(FingerprintModel):
    """Fine-tuned Qwen2.5-7B for fingerprint similarity + outcome
    prediction. Sprint 15 bake-off scope per VISION.md.

    Constructor accepts a HuggingFace model identifier or local path.
    All methods raise NotImplementedError until a trained checkpoint exists.
    """

    def __init__(self, model_id: str = "Qwen/Qwen2.5-7B"):
        self.model_id = model_id

    def train(self, examples: list[TrainingExample]) -> dict[str, Any]:
        raise NotImplementedError(
            "QwenFingerprintModel.train: Sprint 15 bake-off. "
            "Requires ≥1000 DeployAttempt examples + GPU budget."
        )

    def predict_outcome(self, pat_type: str, repo_full: str) -> Prediction:
        raise NotImplementedError(
            "QwenFingerprintModel.predict_outcome: needs trained checkpoint."
        )

    def embed(self, pat_type: str, repo_full: str) -> list[float]:
        raise NotImplementedError(
            "QwenFingerprintModel.embed: needs trained checkpoint."
        )

    def save(self, path: str) -> None:
        raise NotImplementedError("QwenFingerprintModel.save: no checkpoint.")

    def load(self, path: str) -> None:
        raise NotImplementedError("QwenFingerprintModel.load: no checkpoint.")
