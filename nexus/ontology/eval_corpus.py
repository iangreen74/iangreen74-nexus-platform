"""Layer 3 eval corpus writer — append-only ActionEvent records to S3.

Immutable by IAM policy (explicit Deny on DeleteObject). Partitioned by
year/month/day/tenant for Iceberg/Athena discoverability.

Called from service.py AFTER the transactional write succeeds. If the
S3 write fails, the mutation continues — eval corpus is observability,
not a gate. Failed writes log a warning and return None.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

BUCKET_ENV = "FORGEWING_EVAL_CORPUS_BUCKET"
DEFAULT_BUCKET = "forgewing-eval-corpus-418295677815"


def _s3_client():
    try:
        import boto3
        return boto3.client("s3", region_name="us-east-1")
    except Exception:
        return None


def write_action_event(
    *,
    tenant_id: str,
    project_id: str | None,
    ontology_id: str,
    version_id: str | None,
    object_type: str,
    mutation_kind: str,
    caller: str,
    proposed_via: str,
    old_state: dict[str, Any] | None,
    new_state: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Append one ActionEvent. Returns event_id or None on failure."""
    bucket = os.environ.get(BUCKET_ENV, DEFAULT_BUCKET)
    client = _s3_client()
    if client is None:
        logger.debug("eval_corpus: S3 unavailable, skipping")
        return None

    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    event = {
        "event_id": event_id,
        "event_ts": now.isoformat(),
        "tenant_id": tenant_id,
        "project_id": project_id,
        "ontology_id": ontology_id,
        "version_id": version_id,
        "object_type": object_type,
        "mutation_kind": mutation_kind,
        "caller": caller,
        "proposed_via": proposed_via,
        "old_state": old_state,
        "new_state": new_state,
        "metadata": metadata or {},
    }

    key = (
        f"year={now.year:04d}/month={now.month:02d}/day={now.day:02d}/"
        f"tenant={tenant_id}/events-{event_id}.jsonl"
    )

    try:
        client.put_object(
            Bucket=bucket, Key=key,
            Body=json.dumps(event).encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        logger.info("eval_corpus: wrote %s (%s on %s)",
                     event_id[:8], mutation_kind, object_type)
        return event_id
    except Exception as e:
        logger.warning("eval_corpus: write failed for %s: %s", event_id[:8], e)
        return None
