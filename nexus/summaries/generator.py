"""Summary generators — Haiku-powered compression at three horizons.

Each generator reads source data, builds a Haiku prompt, returns a
compressed summary string. Callers decide whether to persist.

If Haiku fails, a structural fallback is returned (never empty string).
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

HAIKU_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

_DAILY_PROMPT = """Compress this founder's day into a ~100-word narrative \
summary. Write in second person ("You..."). Capture: what they worked on, \
key decisions, emotional arc, what's unresolved. Be warm but honest.

Today's activity:
{activity}

Recent tone markers: {tones}

Write the summary only, no preamble."""

_WEEKLY_PROMPT = """Compress this founder's week into a ~75-word reflective \
summary. Write in second person. Capture: the week's theme, key progress, \
key tension, what shifted. Reference specific days only if pivotal.

Daily digests from this week:
{digests}

Write the summary only, no preamble."""

_MONTHLY_PROMPT = """Compress this founder's month into a ~50-word narrative \
arc. Write in second person. Capture: what defined the month, what shipped, \
what the founder was carrying, what changed in how they work.

Weekly rollups from this month:
{rollups}

Write the summary only, no preamble."""


def _bedrock_client():
    from nexus.config import MODE
    if MODE != "production":
        return None
    try:
        import boto3
        return boto3.client("bedrock-runtime", region_name="us-east-1")
    except Exception:
        return None


def _invoke_haiku(prompt: str) -> str | None:
    """Call Haiku, return text response. None on failure."""
    client = _bedrock_client()
    if client is None:
        return None
    try:
        resp = client.invoke_model(
            modelId=HAIKU_MODEL,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        body = json.loads(resp["body"].read())
        for block in body.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "").strip()
        return None
    except Exception as e:
        log.warning("Haiku invoke failed: %s", e)
        return None


def _read_tone_summary(tenant_id: str) -> str:
    """Read recent tone markers as a comma-separated string."""
    try:
        from nexus.mechanism1.tone_store import read_markers
        markers = read_markers(tenant_id, limit=10)
        if not markers:
            return "(no tone data yet)"
        return ", ".join(m.get("tone", "?") for m in markers)
    except Exception:
        return "(tone data unavailable)"


def _read_recent_activity(tenant_id: str) -> str:
    """Read recent classifier proposals as activity signal."""
    try:
        from nexus.mechanism1.proposals import list_pending
        pending = list_pending(tenant_id)
        if not pending:
            return "(no recent activity captured)"
        lines = []
        for p in pending[:10]:
            lines.append(f"- [{p.get('object_type')}] {p.get('title')}")
        return "\n".join(lines)
    except Exception:
        return "(activity data unavailable)"


def _structural_fallback(
    tenant_id: str, horizon: str, source_data: str,
) -> str:
    """Fallback when Haiku is unavailable — structural summary."""
    line_count = len(source_data.strip().splitlines()) if source_data else 0
    return (
        f"[Auto-generated {horizon} summary] "
        f"{line_count} activity items recorded. "
        f"Haiku compression unavailable — raw data preserved in logs."
    )


def generate_daily_digest(tenant_id: str) -> str:
    """Generate today's daily digest for a tenant.

    Returns a ~100-word summary string. Never returns empty.
    """
    activity = _read_recent_activity(tenant_id)
    tones = _read_tone_summary(tenant_id)
    prompt = _DAILY_PROMPT.format(activity=activity, tones=tones)
    result = _invoke_haiku(prompt)
    if result:
        return result
    return _structural_fallback(tenant_id, "daily", activity)


def generate_weekly_rollup(tenant_id: str) -> str:
    """Generate this week's rollup from daily digests.

    Reads past 7 daily digests, compresses into ~75 words.
    """
    try:
        from nexus.summaries.store import read_past_digests
        dailies = read_past_digests(tenant_id, "daily", limit=7)
    except Exception:
        dailies = []
    if not dailies:
        return _structural_fallback(tenant_id, "weekly", "")
    digests = "\n\n".join(
        f"[{d.get('for_date', '?')}] {d.get('text', '')}"
        for d in dailies
    )
    prompt = _WEEKLY_PROMPT.format(digests=digests)
    result = _invoke_haiku(prompt)
    if result:
        return result
    return _structural_fallback(tenant_id, "weekly", digests)


def generate_monthly_arc(tenant_id: str) -> str:
    """Generate this month's arc from weekly rollups.

    Reads past ~4 weekly rollups, compresses into ~50 words.
    """
    try:
        from nexus.summaries.store import read_past_digests
        weeklies = read_past_digests(tenant_id, "weekly", limit=5)
    except Exception:
        weeklies = []
    if not weeklies:
        return _structural_fallback(tenant_id, "monthly", "")
    rollups = "\n\n".join(
        f"[Week of {w.get('for_date', '?')}] {w.get('text', '')}"
        for w in weeklies
    )
    prompt = _MONTHLY_PROMPT.format(rollups=rollups)
    result = _invoke_haiku(prompt)
    if result:
        return result
    return _structural_fallback(tenant_id, "monthly", rollups)
