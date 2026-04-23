"""Mechanism 1: Inline classifier.

Reads a conversation turn via Haiku per-type prompts, produces
ontology proposal candidates. Every candidate is a suggestion;
dispositions flow through nexus.mechanism1.proposals.dispose().

Bedrock pattern matches nexus/capabilities/investigation.py.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = ("feature", "decision", "hypothesis")
MIN_CONFIDENCE = 0.6
HAIKU_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
AWS_REGION = "us-east-1"


@dataclass
class ProposalCandidate:
    candidate_id: str
    tenant_id: str
    project_id: str | None
    object_type: str
    title: str
    summary: str
    reasoning: str
    confidence: float
    source_turn_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_prompt(object_type: str) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "prompts", f"{object_type}.txt")
    with open(path) as f:
        return f.read()


def _bedrock_client():
    from nexus.config import MODE
    if MODE != "production":
        return None
    try:
        import boto3
        return boto3.client("bedrock-runtime", region_name=AWS_REGION)
    except Exception:
        return None


def _invoke_haiku(prompt_text: str) -> dict[str, Any] | None:
    """Call Haiku, parse JSON response. Returns None on any failure."""
    client = _bedrock_client()
    if client is None:
        return None
    try:
        resp = client.invoke_model(
            modelId=HAIKU_MODEL,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt_text}],
            }),
        )
        body = json.loads(resp["body"].read())
        text = ""
        for block in body.get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                break
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return None
        return json.loads(text[start:end + 1])
    except Exception as e:
        logger.warning("Haiku invoke failed: %s", e)
        return None


def extract(
    *,
    conversation_turn: str,
    conversation_context: str = "",
    tenant_id: str,
    project_id: str | None = None,
    source_turn_id: str | None = None,
) -> list[ProposalCandidate]:
    """Run all type prompts against the turn, return confident candidates.

    Returns empty list most of the time — that is correct. False positives
    are worse than false negatives (annoying founders burns trust).
    """
    candidates: list[ProposalCandidate] = []

    for object_type in SUPPORTED_TYPES:
        try:
            template = _load_prompt(object_type)
            prompt = template.format(
                conversation_turn=conversation_turn,
                conversation_context=conversation_context or "(no prior context)",
            )
            result = _invoke_haiku(prompt)
            if not result:
                continue
            proposal = result.get("proposal")
            if not proposal or not isinstance(proposal, dict):
                continue
            conf = float(proposal.get("confidence", 0))
            if conf < MIN_CONFIDENCE:
                continue
            candidates.append(ProposalCandidate(
                candidate_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                project_id=project_id,
                object_type=object_type,
                title=proposal.get("title", "")[:200],
                summary=proposal.get("summary", "")[:2000],
                reasoning=proposal.get("reasoning", "")[:1000],
                confidence=round(conf, 2),
                source_turn_id=source_turn_id,
            ))
        except Exception as e:
            logger.warning("Classifier error for %s: %s", object_type, e)

    return candidates
