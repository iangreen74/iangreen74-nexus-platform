"""Classify deploy events into ontology proposals.

Success → Feature "proven shippable" proposals
Failure → Bug candidates (Bug not in ontology v0 yet)
Timeout/rollback → Investigation candidates

Output shape matches Mechanism 1's ProposalCandidate so proposals
land in the same classifier_proposals table.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger(__name__)

HAIKU_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SUPPORTED_TYPES = ("Feature", "Decision", "Hypothesis", "Bug", "Investigation")

CLASSIFIER_PROMPT = """You analyze a software deploy event and propose what it tells us about the product's ontology.

Respond with ONLY a JSON array. Each element:
- "object_type": one of Feature, Decision, Hypothesis, Bug, Investigation
- "title": short name (under 80 chars)
- "summary": 1-3 sentence explanation of what the event tells us
- "confidence": float 0.0-1.0

Guidelines:
- SUCCESS: propose the Feature is proven shippable. Skip if generic.
- FAILURE: propose a Bug candidate with error detail.
- TIMEOUT/ROLLBACK: propose an Investigation candidate.
- Return [] if unremarkable (routine redeploy, no new features).
- Prefer fewer, higher-confidence proposals. Below 0.5 is usually noise.

Deploy event:
\"\"\"
{event_json}
\"\"\"

JSON array only:"""


@dataclass
class DeployProposal:
    candidate_id: str
    tenant_id: str
    project_id: str | None
    object_type: str
    title: str
    summary: str
    reasoning: str
    confidence: float
    source_turn_id: str | None  # maps to source_event_id
    source_kind: str = "deploy_event"
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_deploy_event(
    event_detail: dict[str, Any],
    bedrock_client: Any = None,
    model_id: str = HAIKU_MODEL,
) -> list[DeployProposal]:
    """Run Haiku classification. Never raises — returns [] on error."""
    tenant_id = event_detail.get("tenant_id") or ""
    project_id = event_detail.get("project_id")
    event_id = (event_detail.get("event_id")
                or event_detail.get("deploy_id") or "unknown")

    if not tenant_id:
        log.warning("deploy_event missing tenant_id, skipping")
        return []

    if bedrock_client is None:
        import boto3
        bedrock_client = boto3.client("bedrock-runtime", region_name="us-east-1")

    prompt = CLASSIFIER_PROMPT.format(
        event_json=json.dumps(event_detail, indent=2, default=str)[:4000]
    )

    try:
        resp = bedrock_client.invoke_model(
            modelId=model_id,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
    except Exception as e:
        log.warning("deploy_classifier bedrock call failed: %s", e)
        return []

    try:
        body = json.loads(resp["body"].read())
        text = body["content"][0]["text"]
        items = _extract_json_array(text)
        return [
            DeployProposal(
                candidate_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                project_id=project_id,
                object_type=item.get("object_type", "Investigation"),
                title=str(item.get("title", ""))[:200],
                summary=str(item.get("summary", ""))[:2000],
                reasoning=str(item.get("content", item.get("summary", "")))[:2000],
                confidence=float(item.get("confidence", 0.0)),
                source_turn_id=event_id,
            )
            for item in (items or [])
            if isinstance(item, dict) and item.get("confidence", 0) >= 0.5
        ]
    except Exception as e:
        log.warning("deploy_classifier parse failed: %s", e)
        return []


def _extract_json_array(text: str) -> list | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.strip().startswith("```"))
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else [result] if isinstance(result, dict) else []
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or start >= end:
            return []
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
