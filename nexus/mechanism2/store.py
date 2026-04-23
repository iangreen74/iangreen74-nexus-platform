"""Deploy proposal persistence — writes to classifier_proposals table.

Reuses the Mechanism 1 schema. source_kind='deploy_event' on the
raw_candidate JSON distinguishes from Mechanism 1's 'conversation_turn'.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from nexus.mechanism2.classifier import DeployProposal

log = logging.getLogger(__name__)


def enqueue_proposals(
    proposals: Sequence[DeployProposal],
    db_conn: Any,
) -> int:
    """Insert proposals into classifier_proposals.

    Returns count of inserted rows. Errors logged, never raised —
    fire-and-forget from the Lambda's perspective.
    """
    if not proposals:
        return 0

    count = 0
    for p in proposals:
        try:
            with db_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO classifier_proposals "
                    "(candidate_id, tenant_id, project_id, object_type, "
                    "title, summary, reasoning, confidence, "
                    "source_turn_id, raw_candidate, status, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,"
                    "'pending',NOW())",
                    (
                        p.candidate_id,
                        p.tenant_id,
                        p.project_id,
                        p.object_type,
                        p.title,
                        p.summary,
                        p.reasoning,
                        p.confidence,
                        p.source_turn_id,
                        json.dumps(p.to_dict()),
                    ),
                )
            count += 1
        except Exception as e:
            log.warning("enqueue deploy proposal failed (%s): %s",
                        p.tenant_id[:12], e)
            try:
                db_conn.rollback()
            except Exception:
                pass

    try:
        db_conn.commit()
    except Exception:
        pass

    return count
