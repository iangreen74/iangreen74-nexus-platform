"""Lambda bridge: Step Functions → AskCustomer enqueue.

Invoked by the WaitForCustomerAnswer state with waitForTaskToken.
Receives the task token + proposal details, calls enqueue_ask(),
then returns — the execution pauses until resolve_ask() calls
SendTaskSuccess with the token.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entrypoint."""
    from nexus.askcustomer.service import enqueue_ask

    proposal_id = enqueue_ask(
        tenant_id=event["tenant_id"],
        project_id=event.get("project_id"),
        question=event["question"],
        options=event.get("options", []),
        context=event.get("context"),
        task_token=event.get("task_token"),
        execution_arn=event.get("state_machine_execution_arn"),
    )

    logger.info("Enqueued ask %s for %s", proposal_id[:8], event["tenant_id"][:12])
    return {"proposal_id": proposal_id, "status": "pending"}
