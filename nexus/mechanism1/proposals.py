"""Mechanism 1 proposal persistence + disposition.

enqueue_proposal / list_pending / dispose (accept|edit|reject).
Mirrors nexus/askcustomer/service.py Postgres pattern.
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
                    "status, created_at) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'pending',NOW())",
                    (candidate.candidate_id, candidate.tenant_id,
                     candidate.project_id, candidate.object_type,
                     candidate.title, candidate.summary,
                     candidate.reasoning, candidate.confidence,
                     candidate.source_turn_id,
                     json.dumps(candidate.to_dict())))
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


def _mark_disposed(candidate_id, disposition, dispositioned_by,
                   edits=None, reason=None):
    conn = _pg_connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE classifier_proposals SET status=%s, "
                    "dispositioned_by=%s, dispositioned_at=NOW(), "
                    "edits=%s::jsonb, reject_reason=%s "
                    "WHERE candidate_id=%s",
                    (disposition, dispositioned_by,
                     json.dumps(edits) if edits else None,
                     reason, candidate_id))
    finally:
        conn.close()


def dispose(
    candidate_id: str,
    disposition: str,
    *,
    edits: dict[str, Any] | None = None,
    reason: str | None = None,
    dispositioned_by: str,
) -> dict[str, Any]:
    """Accept/edit/reject a proposal. Writes ontology + ActionEvent."""
    if disposition not in ("accepted", "edited", "rejected"):
        raise ValueError(f"Invalid disposition: {disposition}")

    candidate = _fetch_candidate(candidate_id)
    ontology_id = None
    version_id = None

    if disposition in ("accepted", "edited"):
        from nexus.ontology.service import propose_object
        props = {
            "title": candidate["title"],
            "summary": candidate["summary"],
        }
        if disposition == "edited" and edits:
            props.update(edits)
        proposed_via = (
            "classifier_m1" if disposition == "accepted"
            else "classifier_m1_edited"
        )
        result = propose_object(
            object_type=candidate["object_type"],
            tenant_id=candidate["tenant_id"],
            properties=props,
            actor=dispositioned_by,
            project_id=candidate["project_id"],
        )
        ontology_id = result["object_id"]
        version_id = result["version_id"]

    _mark_disposed(candidate_id, disposition, dispositioned_by,
                   edits=edits, reason=reason)

    try:
        from nexus.ontology.eval_corpus import write_action_event
        write_action_event(
            tenant_id=candidate["tenant_id"],
            project_id=candidate["project_id"],
            ontology_id=ontology_id or candidate_id,
            version_id=str(version_id) if version_id else candidate_id,
            object_type="classifier_proposal",
            mutation_kind=disposition,
            caller=dispositioned_by,
            proposed_via="classifier_m1",
            old_state=candidate["raw_candidate"],
            new_state=(
                edits if disposition == "edited"
                else None if disposition == "rejected"
                else {"accepted": True}
            ),
            metadata={
                "confidence": candidate["confidence"],
                "source_turn_id": candidate["source_turn_id"],
                "reject_reason": reason if disposition == "rejected" else None,
                "original_object_type": candidate["object_type"],
            },
        )
    except Exception as e:
        logger.warning("eval_corpus write failed for %s: %s",
                       candidate_id[:8], e)

    logger.info("classifier: %s %s by %s",
                disposition, candidate_id[:8], dispositioned_by)
    return {
        "candidate_id": candidate_id,
        "disposition": disposition,
        "ontology_id": ontology_id,
        "version_id": version_id,
    }
