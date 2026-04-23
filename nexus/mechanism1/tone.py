"""Tone classifier — extracts emotional texture from founder turns.

Works alongside the proposal classifier. For each founder turn, runs a
Haiku prompt that classifies tone, urgency, seeking, and entity mentions.

This is NOT a replacement for the proposal classifier. It runs in parallel
on the same turn, producing separate output.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

HAIKU_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

TONE_PROMPT = """You classify the emotional texture of a founder message. \
Read the message and respond with ONLY a JSON object, no other text.

Fields:
- "tone": one of {{relaxed, curious, excited, anxious, frustrated, \
decisive, uncertain, confident, defeated, energized}}
- "urgency": one of {{low, medium, high}}
- "seeking": one of {{advice, validation, execution, company, clarity, \
nothing_specific}}
- "mentions": array of named entities mentioned (empty if none)
- "confidence": float 0.0-1.0

Guidelines:
- advice = asking what they should do
- validation = wants to hear they're on the right track
- execution = wants the system to just do the thing
- company = processing out loud, doesn't need an answer
- clarity = trying to understand, needs explanation

Founder message:
\"\"\"
{message}
\"\"\"

JSON only."""


@dataclass
class ToneMarker:
    """Emotional-texture tag for a single founder turn."""
    tenant_id: str
    turn_id: str
    tone: str
    urgency: str
    seeking: str
    mentions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    timestamp: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def classify_tone(
    message: str,
    tenant_id: str,
    turn_id: str,
    bedrock_client=None,
) -> ToneMarker | None:
    """Run tone classification on a single founder message.

    Returns ToneMarker on success, None on failure. Caller should log
    but not propagate — tone capture is fire-and-forget.
    """
    if not message or not message.strip():
        return None

    if bedrock_client is None:
        from nexus.config import MODE
        if MODE != "production":
            return None
        import boto3
        bedrock_client = boto3.client("bedrock-runtime", region_name="us-east-1")

    prompt = TONE_PROMPT.format(message=message.strip()[:4000])
    try:
        resp = bedrock_client.invoke_model(
            modelId=HAIKU_MODEL,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        body = json.loads(resp["body"].read())
        text = body["content"][0]["text"]
        data = _extract_json(text)
        if not data:
            return None
        return ToneMarker(
            tenant_id=tenant_id,
            turn_id=turn_id,
            tone=data.get("tone", "uncertain"),
            urgency=data.get("urgency", "low"),
            seeking=data.get("seeking", "nothing_specific"),
            mentions=data.get("mentions") or [],
            confidence=float(data.get("confidence", 0.0)),
        )
    except Exception as e:
        log.warning("tone_classifier failed: %s", e)
        return None


def _extract_json(text: str) -> dict | None:
    """Tolerant JSON extraction — Haiku sometimes wraps in markdown."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.strip().startswith("```"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or start >= end:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
