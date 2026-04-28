"""Mechanism 1 proposal disposition: accept / edit / reject.

Split from ``nexus.mechanism1.proposals`` in PR-B (Bug 4 rigorous fix)
to keep both files under the 200-line CI invariant when classifier
columns expanded for Decision/Hypothesis. Persistence (``enqueue``,
``list_pending``, ``_fetch_candidate``) stays in ``proposals``;
disposition (the ontology call + ActionEvent emit on Accept/Edit) moves
here.

``proposed_via`` is the *implementation tag* — free-form, versioned,
e.g. ``classifier_m1`` / ``classifier_m1_edited``. Distinct from
``source_kind`` (column on classifier_proposals, hardcoded
``conversation_classifier`` at INSERT time per migration 012).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from nexus.mechanism1.proposals import _fetch_candidate, _pg_connect

logger = logging.getLogger(__name__)


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
