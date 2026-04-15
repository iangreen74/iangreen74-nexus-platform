"""
Timeline Resolution — ACTIVE/RESOLVED/SUPERSEDED markers for DiagnosisHistory.

Each scheduled diagnosis writes a new OverwatchDiagnosisHistory node.
This module, called right before that write, reaches back at the most
recent ACTIVE node, marks it SUPERSEDED, and records which of its
findings are RESOLVED (present-then-absent in the new run). The
dashboard renders RESOLVED entries muted with a green check so
operators can see a problem actually closing out — not just a new
diagnosis appearing next to an old one.

The diff is deliberately string-based on `key_findings` (the same
human-readable strings the timeline already shows). Treating each entry
as an opaque key is good enough for supersession without committing to
a finding taxonomy we'd have to maintain.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.config import MODE

logger = logging.getLogger("nexus.capabilities.timeline_resolution")

_LABEL = "OverwatchDiagnosisHistory"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_findings(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return [str(x) for x in parsed] if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def supersede_prior_active(new_findings: list[str]) -> list[str]:
    """
    Mark the most-recent ACTIVE diagnosis as SUPERSEDED. Return the list
    of its findings that are now RESOLVED (i.e. absent from new_findings).
    """
    new_set = set(new_findings or [])
    now = _now_iso()

    if MODE != "production":
        with overwatch_graph._lock:
            rows = [n for n in overwatch_graph._local_store.get(_LABEL, []) or []
                    if n.get("resolution_status") == "ACTIVE"]
            if not rows:
                return []
            rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
            prior = rows[0]
            prior_findings = _parse_findings(prior.get("key_findings"))
            resolved = [f for f in prior_findings if f not in new_set]
            prior["resolution_status"] = "SUPERSEDED"
            prior["resolved_at"] = now
            prior["resolved_findings"] = json.dumps(resolved)
            return resolved

    try:
        rows = overwatch_graph.query(
            f"MATCH (n:{_LABEL}) WHERE n.resolution_status = 'ACTIVE' "
            "RETURN n.id AS id, n.key_findings AS key_findings "
            "ORDER BY n.created_at DESC LIMIT 1"
        ) or []
    except Exception:
        logger.exception("supersede_prior_active: read failed")
        return []
    if not rows:
        return []
    prior = rows[0]
    prior_findings = _parse_findings(prior.get("key_findings"))
    resolved = [f for f in prior_findings if f not in new_set]
    try:
        overwatch_graph.query(
            f"MATCH (n:{_LABEL} {{id: $id}}) "
            "SET n.resolution_status = 'SUPERSEDED', "
            "n.resolved_at = $now, n.resolved_findings = $resolved",
            {"id": prior.get("id"), "now": now,
             "resolved": json.dumps(resolved)},
        )
    except Exception:
        logger.exception("supersede_prior_active: write failed")
    return resolved
