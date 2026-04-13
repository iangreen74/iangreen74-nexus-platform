"""Shared helpers for parsing Bedrock model responses.

Bedrock sometimes wraps JSON in markdown fences or adds preamble text;
parse_bedrock_json strips those before json.loads and returns a fallback
on failure rather than raising.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_bedrock_json(text: str, fallback: dict | None = None) -> dict:
    """Parse JSON from a Bedrock response, tolerating fences/preamble."""
    fb = fallback if fallback is not None else {}
    if not text:
        return fb

    text = text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    if not text.startswith("{") and not text.startswith("["):
        brace = text.find("{")
        bracket = text.find("[")
        if brace >= 0 and (bracket < 0 or brace < bracket):
            text = text[brace:]
        elif bracket >= 0:
            text = text[bracket:]

    if text.startswith("{"):
        depth = 0
        for i, c in enumerate(text):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    text = text[: i + 1]
                    break

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Bedrock JSON parse failed: %s. Raw: %s", exc, text[:200])
        out = dict(fb)
        out.setdefault("error", f"JSON parse failed: {exc}")
        out.setdefault("raw", text[:500])
        return out


def parse_bedrock_json_array(text: str, fallback: list | None = None) -> list:
    """Parse a JSON array from a Bedrock response. Returns fallback on failure."""
    fb = fallback if fallback is not None else []
    if not text:
        return fb

    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    bracket = text.find("[")
    if bracket > 0:
        text = text[bracket:]

    if text.startswith("["):
        depth = 0
        for i, c in enumerate(text):
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    text = text[: i + 1]
                    break

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else fb
    except json.JSONDecodeError as exc:
        logger.warning("Bedrock JSON array parse failed: %s. Raw: %s", exc, text[:200])
        return fb
