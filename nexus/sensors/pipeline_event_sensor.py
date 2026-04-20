"""Pipeline Event Sensor — Overwatch Layer 1 consumer for Phase C events.

Polls SQS queue subscribed to forgewing-deploy-events EventBridge bus,
validates envelope v1.0, writes PipelineEvent nodes via
record_pipeline_event, deletes processed messages. Step 7 of
run_deploy_cycle.

At-least-once: EventBridge->SQS can redeliver. Writes are MERGE-by-id.

Local mode (no PIPELINE_EVENTS_QUEUE_URL): returns skipped immediately.

Poison messages (bad JSON, missing fields): deleted to unblock queue.
Neptune write failures: message left for SQS redelivery.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

from nexus.overwatch_graph import record_pipeline_event

log = logging.getLogger(__name__)

_REQUIRED_FIELDS = (
    "event_id", "event_type", "event_version",
    "emitted_at", "tenant_id", "project_id", "correlation_id",
)


def _get_queue_url() -> str:
    return os.getenv("PIPELINE_EVENTS_QUEUE_URL", "").strip()


def _sqs_client():
    return boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-1"))


def _parse_envelope(body: str) -> dict | None:
    try:
        outer = json.loads(body)
    except (ValueError, TypeError):
        log.warning("pipeline_event_sensor: non-JSON SQS body")
        return None
    envelope = outer.get("detail") if isinstance(outer, dict) else None
    if not isinstance(envelope, dict):
        log.warning("pipeline_event_sensor: no 'detail' in SQS body")
        return None
    missing = [f for f in _REQUIRED_FIELDS if not envelope.get(f)]
    if missing:
        log.warning("pipeline_event_sensor: missing fields: %s", missing)
        return None
    return envelope


def _record(envelope: dict) -> bool:
    try:
        record_pipeline_event(
            event_id=envelope["event_id"],
            event_type=envelope["event_type"],
            correlation_id=envelope["correlation_id"],
            tenant_id=envelope["tenant_id"],
            project_id=envelope["project_id"],
            emitted_at=envelope["emitted_at"],
            payload=envelope.get("payload") or {},
            feature_id=envelope.get("feature_id"),
        )
        return True
    except Exception:
        log.exception("pipeline_event_sensor: record failed for %s",
                      envelope.get("event_id"))
        return False


def poll_pipeline_events() -> dict[str, Any]:
    """One poll cycle. Returns counts for observability."""
    queue_url = _get_queue_url()
    if not queue_url:
        return {"skipped": True, "reason": "no_queue_url_configured"}

    sqs = _sqs_client()
    try:
        response = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=5,
        )
    except Exception:
        log.exception("pipeline_event_sensor: ReceiveMessage failed")
        return {"polled": 0, "recorded": 0, "poison": 0, "errors": 1}

    messages = response.get("Messages") or []
    recorded = poison = errors = 0
    to_delete: list[dict] = []

    for msg in messages:
        envelope = _parse_envelope(msg.get("Body", ""))
        if envelope is None:
            poison += 1
            to_delete.append({"Id": msg["MessageId"], "ReceiptHandle": msg["ReceiptHandle"]})
            continue
        if _record(envelope):
            recorded += 1
            to_delete.append({"Id": msg["MessageId"], "ReceiptHandle": msg["ReceiptHandle"]})
        else:
            errors += 1

    if to_delete:
        try:
            sqs.delete_message_batch(QueueUrl=queue_url, Entries=to_delete)
        except Exception:
            log.exception("pipeline_event_sensor: DeleteMessageBatch failed")
            errors += 1

    return {"polled": len(messages), "recorded": recorded, "poison": poison, "errors": errors}
