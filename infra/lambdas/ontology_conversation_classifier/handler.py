"""EventBridge subscriber for conversation_turn events.

Triggered by every conversation_turn event published to
forgewing-ontology-events. For each event:
  1. Parse event detail
  2. Call classifier.extract() against the message
  3. For each confident candidate, enqueue_proposal()

All failures are logged and swallowed. We NEVER want a failure here to
poison the EventBridge retry path or blow up a Lambda invocation.
"""
import json
import logging
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """EventBridge event handler.

    Event shape (from put_events):
      {
        "source": "aria-platform.conversation",
        "detail-type": "conversation_turn",
        "detail": {
          "tenant_id": "...",
          "project_id": "...",
          "session_id": "...",
          "turn_id": "...",
          "role": "user",
          "message": "...",
          "conversation_context": "...",
          "published_at": "..."
        }
      }
    """
    detail = event.get("detail") or {}
    tenant_id = detail.get("tenant_id")
    project_id = detail.get("project_id")
    role = detail.get("role")
    message = detail.get("message") or ""
    turn_id = detail.get("turn_id")
    context_str = detail.get("conversation_context") or ""

    if role != "user":
        logger.info("Skipping non-user turn (role=%s)", role)
        return {"statusCode": 200, "skipped": True, "reason": "non-user role"}

    if not tenant_id or not message:
        logger.warning("Skipping event: tenant_id=%s message_len=%d",
                       tenant_id, len(message))
        return {"statusCode": 200, "skipped": True, "reason": "missing fields"}

    try:
        from nexus.mechanism1.classifier import extract
        from nexus.mechanism1.proposals import enqueue_proposal
    except Exception as e:
        logger.error("Failed to import classifier/proposals: %s", e,
                     exc_info=True)
        return {"statusCode": 200, "skipped": True, "reason": "import failed"}

    try:
        candidates = extract(
            conversation_turn=message,
            conversation_context=context_str,
            tenant_id=tenant_id,
            project_id=project_id,
            source_turn_id=turn_id,
        )
    except Exception as e:
        logger.error("classifier.extract failed for tenant=%s: %s",
                     tenant_id, e, exc_info=True)
        return {"statusCode": 200, "skipped": True,
                "reason": "classifier failed"}

    logger.info("Extracted %d candidate(s) for tenant=%s",
                len(candidates), tenant_id)

    enqueued = 0
    failed = 0
    for candidate in candidates:
        try:
            enqueue_proposal(candidate)
            enqueued += 1
        except Exception as e:
            failed += 1
            logger.warning("Failed to enqueue %s: %s",
                           getattr(candidate, "candidate_id", "?"), e)

    # Tone classification — fire-and-forget, never blocks
    tone_result = None
    try:
        from nexus.mechanism1.tone import classify_tone
        from nexus.mechanism1.tone_store import save_marker
        marker = classify_tone(
            message=message, tenant_id=tenant_id, turn_id=turn_id,
        )
        if marker:
            save_marker(marker)
            tone_result = marker.tone
            logger.info("tone captured: %s (conf=%.2f)",
                        marker.tone, marker.confidence)
    except Exception as e:
        logger.warning("tone capture failed (non-blocking): %s", e)

    return {
        "statusCode": 200,
        "enqueued": enqueued,
        "failed": failed,
        "tone": tone_result,
        "tenant_id": tenant_id,
    }
