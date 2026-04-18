"""Dataset loader — pulls DeployAttempt nodes from Neptune into typed
training examples. Fully implemented; returns empty iterators when the
corpus is unpopulated.

Ground-truth schema (from aria deploy_attempt_recorder.py, not docs):
  DeployAttempt node properties:
    tenant_id, attempt_id, project_id  — identity
    pat_type, repo_full, fingerprint   — input features
    started_at, ended_at               — timing
    deploy_success                     — outcome label
    correction_count, template_quality_score  — quality signals
    error_message                      — failure context
    final_template                     — successful CFN template
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from nexus import neptune_client
from nexus.config import MODE

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrainingExample:
    """One labeled deployment example."""
    attempt_id: str
    tenant_id: str
    project_id: str
    pat_type: str
    repo_full: str
    fingerprint: str
    deploy_success: bool
    correction_count: int
    template_quality_score: float
    error_message: str
    started_at: str
    ended_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


def load_training_examples(
    *,
    since: str | None = None,
    until: str | None = None,
    pat_type: str | None = None,
    outcome: bool | None = None,
    limit: int = 5000,
) -> list[TrainingExample]:
    """Pull DeployAttempt nodes from Neptune and materialize as examples.

    Filters are AND-combined. Returns an empty list when Neptune is
    unreachable or the corpus is unpopulated — callers should check
    len() before training.
    """
    where_clauses = ["d.ended_at IS NOT NULL"]
    params: dict[str, Any] = {"limit": limit}

    if since:
        where_clauses.append("d.started_at >= $since")
        params["since"] = since
    if until:
        where_clauses.append("d.started_at <= $until")
        params["until"] = until
    if pat_type:
        where_clauses.append("d.pat_type = $pat_type")
        params["pat_type"] = pat_type
    if outcome is not None:
        where_clauses.append("d.deploy_success = $outcome")
        params["outcome"] = outcome

    where = " AND ".join(where_clauses)
    cypher = (
        f"MATCH (d:DeployAttempt) WHERE {where} "
        "RETURN d.attempt_id AS attempt_id, d.tenant_id AS tenant_id, "
        "d.project_id AS project_id, d.pat_type AS pat_type, "
        "d.repo_full AS repo_full, d.fingerprint AS fingerprint, "
        "d.deploy_success AS deploy_success, "
        "d.correction_count AS correction_count, "
        "d.template_quality_score AS template_quality_score, "
        "d.error_message AS error_message, "
        "d.started_at AS started_at, d.ended_at AS ended_at "
        "ORDER BY d.started_at DESC LIMIT $limit"
    )

    rows = neptune_client.query(cypher, params) or []
    examples: list[TrainingExample] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("attempt_id"):
            continue
        examples.append(TrainingExample(
            attempt_id=r.get("attempt_id", ""),
            tenant_id=r.get("tenant_id", ""),
            project_id=r.get("project_id") or "",
            pat_type=r.get("pat_type") or "",
            repo_full=r.get("repo_full") or "",
            fingerprint=r.get("fingerprint") or "",
            deploy_success=bool(r.get("deploy_success")),
            correction_count=int(r.get("correction_count") or 0),
            template_quality_score=float(r.get("template_quality_score") or 0.0),
            error_message=r.get("error_message") or "",
            started_at=r.get("started_at") or "",
            ended_at=r.get("ended_at") or "",
        ))

    logger.info("dataset: loaded %d training examples (filters: %s)",
                len(examples), {k: v for k, v in params.items() if k != "limit"})
    return examples


def corpus_stats(examples: list[TrainingExample]) -> dict[str, Any]:
    """Summary statistics for a loaded corpus."""
    if not examples:
        return {"total": 0, "success": 0, "failure": 0, "pat_types": []}
    successes = sum(1 for e in examples if e.deploy_success)
    pat_types = sorted({e.pat_type for e in examples if e.pat_type})
    return {
        "total": len(examples),
        "success": successes,
        "failure": len(examples) - successes,
        "success_rate": round(successes / len(examples), 3),
        "pat_types": pat_types,
        "date_range": (examples[-1].started_at[:10], examples[0].started_at[:10]),
    }
