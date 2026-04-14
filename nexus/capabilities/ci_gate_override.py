"""
CI Gate Override — operator escape hatch for the deploy gate.

The 8-factor readiness engine can be too conservative when the rolling
CI green-rate window is dragged down by historical failures even though
the commits being deployed are individually green (2026-04-14 morning
outage). This module lets an operator set a time-boxed manual override
that evaluate_ci_gate honors before any of the normal checks run.

Storage is a singleton Neptune node — key='active' — so a new override
atomically replaces any existing one. Expiry is enforced on read; no
background sweeper needed. In local mode the override lives in a single
in-memory dict so unit tests can exercise the full flow without AWS.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.config import MODE

logger = logging.getLogger("nexus.capabilities.ci_gate_override")

_LABEL = "OverwatchCIGateOverride"
_KEY = "active"
MAX_DURATION_MINUTES = 24 * 60  # hard ceiling — no "forever" overrides.

# Local-mode single-slot store. A lock makes set/clear/get race-safe for
# the test suite (which resets the global graph state between cases).
_local_slot: dict[str, Any] | None = None
_local_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def set_override(
    decision: str,
    reason: str,
    duration_minutes: int = 60,
) -> dict[str, Any]:
    """
    Replace any existing CI gate override with a new time-boxed one.
    Decision must be DEPLOY or HOLD. Raises ValueError on invalid input.
    """
    global _local_slot
    dec = (decision or "").strip().upper()
    if dec not in ("DEPLOY", "HOLD"):
        raise ValueError(f"decision must be DEPLOY or HOLD (got {decision!r})")
    try:
        minutes = int(duration_minutes)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration_minutes must be an integer") from exc
    if minutes <= 0:
        raise ValueError("duration_minutes must be positive")
    if minutes > MAX_DURATION_MINUTES:
        raise ValueError(f"duration_minutes must be <= {MAX_DURATION_MINUTES}")
    reason_clean = (reason or "").strip()[:500]
    if not reason_clean:
        raise ValueError("reason is required")

    now = _now()
    expires = now + timedelta(minutes=minutes)
    payload = {
        "key": _KEY,
        "decision": dec,
        "reason": reason_clean,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "duration_minutes": minutes,
    }

    if MODE != "production":
        with _local_lock:
            _local_slot = dict(payload)
        logger.info("ci_gate_override set (local): %s until %s",
                    dec, expires.isoformat())
        return dict(payload)

    try:
        overwatch_graph.query(
            f"MERGE (o:{_LABEL} {{key: $key}}) "
            "SET o.decision = $decision, o.reason = $reason, "
            "o.created_at = $created_at, o.expires_at = $expires_at, "
            "o.duration_minutes = $duration_minutes",
            payload,
        )
    except Exception:
        logger.exception("ci_gate_override MERGE failed")
        raise
    logger.info("ci_gate_override set: %s until %s", dec, expires.isoformat())
    return payload


def get_active_override() -> dict[str, Any] | None:
    """Return the active override if one exists and hasn't expired, else None."""
    if MODE != "production":
        with _local_lock:
            row = dict(_local_slot) if _local_slot else None
    else:
        try:
            rows = overwatch_graph.query(
                f"MATCH (o:{_LABEL} {{key: $key}}) "
                "RETURN o.decision AS decision, o.reason AS reason, "
                "o.created_at AS created_at, o.expires_at AS expires_at, "
                "o.duration_minutes AS duration_minutes",
                {"key": _KEY},
            ) or []
        except Exception:
            logger.exception("ci_gate_override read failed")
            return None
        row = rows[0] if rows else None
    if not row:
        return None
    expires = _parse_iso(row.get("expires_at"))
    if expires is None or expires <= _now():
        return None
    return row


def clear_override() -> bool:
    """Delete the active override. Returns True if something was removed."""
    global _local_slot
    if MODE != "production":
        with _local_lock:
            if _local_slot is None:
                return False
            _local_slot = None
            return True
    try:
        overwatch_graph.query(
            f"MATCH (o:{_LABEL} {{key: $key}}) DETACH DELETE o",
            {"key": _KEY},
        )
    except Exception:
        logger.exception("ci_gate_override clear failed")
        return False
    return True


def reset_local_override() -> None:
    """Test hook — clears the in-memory slot. No-op in production."""
    global _local_slot
    with _local_lock:
        _local_slot = None
