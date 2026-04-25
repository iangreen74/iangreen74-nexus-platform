"""Conversation turn persistence — Postgres in production, in-memory dict in local mode.

Schema: agent_conversations + agent_conversation_turns (Track E migration 011).
Lazy connect: import psycopg2 only when needed; degrade to local store otherwise.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("nexus.aria_v2.persistence")

# In-memory store for non-production mode or when DB is unreachable.
_local_conversations: dict[str, dict[str, Any]] = {}
_local_turns: dict[str, list[dict[str, Any]]] = {}
_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_production() -> bool:
    return os.environ.get("NEXUS_MODE", "local").lower() == "production"


def _db_url() -> str | None:
    from nexus.aria_v2.db import database_url; return database_url()


@contextmanager
def _connect():
    import psycopg2
    url = _db_url()
    if not url:
        raise RuntimeError("OVERWATCH_V2_DATABASE_URL not set")
    conn = psycopg2.connect(url, connect_timeout=5)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reset_local() -> None:
    """Clear in-memory state. Tests only."""
    with _lock:
        _local_conversations.clear()
        _local_turns.clear()


def ensure_conversation(conversation_id: Optional[str], title: str = "") -> str:
    """Create or look up a conversation; returns its UUID."""
    cid = conversation_id or str(uuid.uuid4())
    if not _is_production() or not _db_url():
        with _lock:
            if cid not in _local_conversations:
                _local_conversations[cid] = {
                    "conversation_id": cid, "title": title or "(untitled)",
                    "started_at": _now_iso(), "last_active_at": _now_iso(),
                    "turn_count": 0, "status": "active", "tags": [],
                }
                _local_turns[cid] = []
        return cid
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_conversations (conversation_id, title) VALUES (%s, %s) "
                "ON CONFLICT (conversation_id) DO NOTHING",
                (cid, title or "(untitled)"),
            )
    except Exception:
        log.exception("ensure_conversation Postgres failed; persistence degraded")
    return cid


def append_turn(
    conversation_id: str, role: str, content: dict,
    tool_calls: Optional[list] = None,
    tokens_in: int = 0, tokens_out: int = 0,
    cost_usd: float = 0.0,
) -> dict:
    """Append a turn. Returns the persisted row (with turn_index)."""
    if not _is_production() or not _db_url():
        with _lock:
            turns = _local_turns.setdefault(conversation_id, [])
            row = {
                "turn_id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "turn_index": len(turns),
                "role": role, "content": content,
                "tool_calls": tool_calls or [],
                "tokens_in": tokens_in, "tokens_out": tokens_out,
                "cost_usd": cost_usd, "created_at": _now_iso(),
            }
            turns.append(row)
            if conversation_id in _local_conversations:
                _local_conversations[conversation_id]["turn_count"] = len(turns)
                _local_conversations[conversation_id]["last_active_at"] = row["created_at"]
            return row
    try:
        import psycopg2.extras
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(turn_index), -1) + 1 FROM agent_conversation_turns "
                    "WHERE conversation_id = %s",
                    (conversation_id,),
                )
                turn_index = int(cur.fetchone()[0])
                turn_id = str(uuid.uuid4())
                cur.execute(
                    """INSERT INTO agent_conversation_turns
                       (turn_id, conversation_id, turn_index, role, content,
                        tool_calls, tokens_in, tokens_out, cost_usd)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (turn_id, conversation_id, turn_index, role,
                     psycopg2.extras.Json(content),
                     psycopg2.extras.Json(tool_calls or []),
                     tokens_in, tokens_out, cost_usd),
                )
                cur.execute(
                    """UPDATE agent_conversations
                       SET last_active_at = now(), turn_count = turn_count + 1
                       WHERE conversation_id = %s""",
                    (conversation_id,),
                )
        return {"turn_id": turn_id, "conversation_id": conversation_id,
                "turn_index": turn_index, "role": role, "content": content,
                "tool_calls": tool_calls or [], "tokens_in": tokens_in,
                "tokens_out": tokens_out, "cost_usd": cost_usd,
                "created_at": _now_iso()}
    except Exception:
        log.exception("append_turn Postgres failed; falling back to local store")
        # Best-effort local persistence so the reasoner still progresses.
        return append_turn(conversation_id, role, content, tool_calls,
                           tokens_in, tokens_out, cost_usd)


def list_turns(conversation_id: str, limit: int = 100) -> list[dict]:
    if not _is_production() or not _db_url():
        with _lock:
            return list(_local_turns.get(conversation_id, []))[-limit:]
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT turn_index, role, content, tool_calls,
                              tokens_in, tokens_out, cost_usd, created_at
                       FROM agent_conversation_turns
                       WHERE conversation_id = %s
                       ORDER BY turn_index ASC
                       LIMIT %s""",
                    (conversation_id, limit),
                )
                rows = cur.fetchall()
        return [{"turn_index": r[0], "role": r[1],
                 "content": r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}"),
                 "tool_calls": r[3] if isinstance(r[3], list) else json.loads(r[3] or "[]"),
                 "tokens_in": r[4], "tokens_out": r[5],
                 "cost_usd": float(r[6]) if r[6] is not None else 0.0,
                 "created_at": r[7].isoformat() if r[7] else None}
                for r in rows]
    except Exception:
        log.exception("list_turns Postgres failed; using local store")
        return list_turns(conversation_id, limit)

def list_conversations(limit: int = 50) -> list[dict]:
    if not _is_production() or not _db_url():
        with _lock:
            convs = sorted(_local_conversations.values(),
                           key=lambda c: c["last_active_at"], reverse=True)
            return convs[:limit]
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT conversation_id, title, started_at, last_active_at,
                              turn_count, status
                       FROM agent_conversations
                       ORDER BY last_active_at DESC LIMIT %s""",
                    (limit,),
                )
                rows = cur.fetchall()
        return [{"conversation_id": r[0], "title": r[1],
                 "started_at": r[2].isoformat() if r[2] else None,
                 "last_active_at": r[3].isoformat() if r[3] else None,
                 "turn_count": r[4], "status": r[5]} for r in rows]
    except Exception:
        log.exception("list_conversations Postgres failed; using local store")
        return list_conversations(limit)
