"""Reads pending Socratic prompts for ARIA prompt assembly.

Separate module from ontology_reader.py to keep that file under the
200-line CI limit. Called by prompt_assembly to inject a section.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def read_pending_socratic_prompts(
    tenant_id: str, limit: int = 3,
) -> list[dict[str, Any]]:
    """Return up to N pending Socratic prompts, priority-sorted.

    Returns empty list on any error — prompt_assembly handles gracefully.
    """
    try:
        from nexus.mechanism3.store import read_pending_prompts
        conn = _pg_connect()
        if conn is None:
            return []
        try:
            return read_pending_prompts(tenant_id, conn, limit=limit)
        finally:
            conn.close()
    except Exception as e:
        log.warning("socratic_reader fallback: %s", e)
        return []


def _pg_connect():
    """Open Postgres connection using DATABASE_URL."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    try:
        import psycopg2
        return psycopg2.connect(url, connect_timeout=5)
    except Exception as e:
        log.warning("socratic_reader pg_connect failed: %s", e)
        return None


def build_socratic_section(
    prompts: list[dict[str, Any]],
) -> tuple[str, str, int]:
    """Format pending prompts into a prompt section tuple.

    Returns (name, text, trim_priority). Priority 60 = trimmed before
    history (100) but after tone (50) and memory (40).
    """
    if not prompts:
        return ("socratic", "", 60)
    lines = ["# What you might want to think about\n"]
    for p in prompts:
        q = (p.get("question") or "").strip()
        if q:
            lines.append(f"- {q}")
    if len(lines) == 1:
        return ("socratic", "", 60)
    return ("socratic", "\n".join(lines) + "\n", 60)
