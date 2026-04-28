"""Mechanism 1 proposal persistence.

enqueue_proposal / list_pending / _fetch_candidate. Mirrors
nexus/askcustomer/service.py Postgres pattern. Dispositions
(accept/edit/reject) live in ``nexus.mechanism1.disposition`` — split
from this module in PR-B (Bug 4 rigorous fix) to keep both files under
the 200-line CI invariant after Decision/Hypothesis columns landed.

``source_kind`` is hardcoded to ``'conversation_classifier'`` at the
INSERT site below — this writer only runs in service of mechanism 1,
so the value is fixed (migration 012 reserves the enum values).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from nexus.mechanism1.classifier import ProposalCandidate

logger = logging.getLogger(__name__)


class ClassifierNotConfiguredError(RuntimeError):
    """DATABASE_URL not set."""


def _pg_connect():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise ClassifierNotConfiguredError(
            "DATABASE_URL not set — classifier requires Postgres"
        )
    import psycopg2
    return psycopg2.connect(url, connect_timeout=5)


def enqueue_proposal(candidate: ProposalCandidate) -> str:
    """Write a pending proposal. Returns candidate_id."""
    conn = _pg_connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO classifier_proposals (candidate_id, "
                    "tenant_id, project_id, object_type, title, summary, "
                    "reasoning, confidence, source_turn_id, raw_candidate, "
                    "context, choice_made, decided_at, decided_by, "
                    "alternatives_considered, statement, why_believed, "
                    "how_will_be_tested, status, source_kind, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,"
                    "%s,%s,%s,%s,%s,'pending','conversation_classifier',NOW())",
                    (candidate.candidate_id, candidate.tenant_id,
                     candidate.project_id, candidate.object_type,
                     candidate.title, candidate.summary,
                     candidate.reasoning, candidate.confidence,
                     candidate.source_turn_id,
                     json.dumps(candidate.to_dict()),
                     candidate.context, candidate.choice_made,
                     candidate.decided_at, candidate.decided_by,
                     candidate.alternatives_considered, candidate.statement,
                     candidate.why_believed, candidate.how_will_be_tested))
    finally:
        conn.close()
    logger.info("classifier: enqueued %s (%s) for %s",
                candidate.candidate_id[:8], candidate.object_type,
                candidate.tenant_id[:12])
    return candidate.candidate_id


def list_pending(
    tenant_id: str,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """List pending proposals for a tenant."""
    try:
        conn = _pg_connect()
    except ClassifierNotConfiguredError:
        return []
    cols = ("candidate_id, object_type, title, summary, "
            "reasoning, confidence, source_turn_id, created_at")
    where = "tenant_id = %s AND status = 'pending'"
    params: tuple = (tenant_id,)
    if project_id:
        where += " AND project_id = %s"
        params = (tenant_id, project_id)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {cols} FROM classifier_proposals "
                    f"WHERE {where} ORDER BY created_at DESC",
                    params,
                )
                return [
                    {"candidate_id": str(r[0]), "object_type": r[1],
                     "title": r[2], "summary": r[3], "reasoning": r[4],
                     "confidence": float(r[5]) if r[5] else None,
                     "source_turn_id": r[6],
                     "created_at": r[7].isoformat() if r[7] else None}
                    for r in cur.fetchall()
                ]
    finally:
        conn.close()


def _fetch_candidate(candidate_id: str) -> dict[str, Any]:
    """Fetch a pending proposal. Raises ValueError if missing or disposed."""
    conn = _pg_connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT candidate_id, tenant_id, project_id, object_type, "
                    "title, summary, reasoning, confidence, source_turn_id, "
                    "status, raw_candidate FROM classifier_proposals "
                    "WHERE candidate_id = %s FOR UPDATE", (candidate_id,))
                row = cur.fetchone()
        if not row:
            raise ValueError(f"Proposal {candidate_id} not found")
        if row[9] != "pending":
            raise ValueError(f"Proposal {candidate_id} is {row[9]}")
        return {"candidate_id": str(row[0]), "tenant_id": row[1],
                "project_id": row[2], "object_type": row[3],
                "title": row[4], "summary": row[5], "reasoning": row[6],
                "confidence": float(row[7]) if row[7] else 0,
                "source_turn_id": row[8], "raw_candidate": row[10]}
    finally:
        conn.close()


