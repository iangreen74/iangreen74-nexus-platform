"""Training loop entry point.

Usage:
    python3 -m nexus.intelligence.training.train --model baseline
    python3 -m nexus.intelligence.training.train --model qwen --since 2026-04-01
    python3 -m nexus.intelligence.training.train --stats-only

The baseline model's train() is a no-op (retrieval-based). The Qwen
model raises NotImplementedError until Sprint 15 lands a checkpoint.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from nexus.intelligence.training.dataset import (
    corpus_stats,
    load_training_examples,
)
from nexus.intelligence.training.model import (
    BaselineHeuristicModel,
    FingerprintModel,
    QwenFingerprintModel,
)

logger = logging.getLogger(__name__)

MODELS: dict[str, type[FingerprintModel]] = {
    "baseline": BaselineHeuristicModel,
    "qwen": QwenFingerprintModel,
}


def run_training(
    model_name: str = "baseline",
    since: str | None = None,
    until: str | None = None,
    checkpoint_dir: str = "./checkpoints",
) -> dict[str, Any]:
    """Load corpus, train model, report metrics."""
    examples = load_training_examples(since=since, until=until)
    stats = corpus_stats(examples)
    logger.info("corpus: %s", stats)

    if stats["total"] == 0:
        return {"status": "no_data", "corpus": stats}

    model_cls = MODELS.get(model_name)
    if not model_cls:
        return {"status": "error", "reason": f"unknown model: {model_name}"}

    model = model_cls()
    try:
        metrics = model.train(examples)
    except NotImplementedError as e:
        return {
            "status": "not_implemented",
            "reason": str(e),
            "corpus": stats,
        }

    model.save(f"{checkpoint_dir}/{model_name}")

    # Evaluation stub — Track B3 (nexus/intelligence/evaluation/) will
    # land proper holdout eval, confusion matrices, and drift detection.
    eval_result: dict[str, Any] = {"note": "evaluation not yet implemented (Track B3)"}

    return {
        "status": "trained",
        "model": model_name,
        "corpus": stats,
        "training_metrics": metrics,
        "evaluation": eval_result,
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Train a fingerprint model on the DeployAttempt corpus.")
    parser.add_argument("--model", choices=list(MODELS.keys()),
                        default="baseline")
    parser.add_argument("--since", help="ISO date filter (e.g. 2026-04-01)")
    parser.add_argument("--until", help="ISO date upper bound")
    parser.add_argument("--checkpoint-dir", default="./checkpoints")
    parser.add_argument("--stats-only", action="store_true",
                        help="Print corpus stats without training")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")

    if args.stats_only:
        examples = load_training_examples(since=args.since, until=args.until)
        stats = corpus_stats(examples)
        for k, v in stats.items():
            print(f"  {k}: {v}")
        return 0

    result = run_training(
        model_name=args.model,
        since=args.since,
        until=args.until,
        checkpoint_dir=args.checkpoint_dir,
    )
    for k, v in result.items():
        print(f"  {k}: {v}")
    return 0 if result.get("status") != "error" else 1


if __name__ == "__main__":
    sys.exit(_cli())
