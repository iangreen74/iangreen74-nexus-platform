"""Socratic prompt persistence. UPSERT by (tenant_id, rule_name, subject_id)."""
from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

from nexus.mechanism3.rules import SocraticPrompt

log = logging.getLogger(__name__)


def save_prompts(prompts: Sequence[SocraticPrompt], db_conn: Any) -> int:
    if not prompts:
        return 0
    count = 0
    for p in prompts:
        try:
            with db_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO socratic_prompts "
                    "(tenant_id,project_id,rule_name,subject_kind,"
                    "subject_id,question,rationale,priority,status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending') "
                    "ON CONFLICT (tenant_id,rule_name,subject_id) "
                    "DO UPDATE SET question=EXCLUDED.question, "
                    "rationale=EXCLUDED.rationale, priority=EXCLUDED.priority "
                    "WHERE socratic_prompts.status IN ('pending','surfaced')",
                    (p.tenant_id, p.project_id, p.rule_name, p.subject_kind,
                     p.subject_id, p.question, p.rationale, p.priority))
            count += 1
        except Exception as e:
            log.warning("save_prompts failed (rule=%s): %s", p.rule_name, e)
            try:
                db_conn.rollback()
            except Exception:
                pass
    try:
        db_conn.commit()
    except Exception:
        pass
    return count


def read_pending_prompts(tenant_id: str, db_conn: Any = None, limit: int = 5) -> list[dict]:
    if db_conn is None:
        return []
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT id,question,rationale,priority,rule_name,project_id "
                "FROM socratic_prompts WHERE tenant_id=%s AND status='pending' "
                "ORDER BY priority DESC, created_at ASC LIMIT %s",
                (tenant_id, limit))
            return [{"id": r[0], "question": r[1], "rationale": r[2],
                     "priority": r[3], "rule_name": r[4], "project_id": r[5]}
                    for r in cur.fetchall()]
    except Exception as e:
        log.warning("read_pending_prompts failed: %s", e)
        return []


def mark_surfaced(prompt_ids: Iterable[int], db_conn: Any) -> int:
    ids = list(prompt_ids)
    if not ids or db_conn is None:
        return 0
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "UPDATE socratic_prompts SET status='surfaced', surfaced_at=NOW() "
                "WHERE id=ANY(%s) AND status='pending'", (ids,))
            count = cur.rowcount
        db_conn.commit()
        return count
    except Exception as e:
        log.warning("mark_surfaced failed: %s", e)
        return 0


def mark_acknowledged(prompt_id: int, db_conn: Any) -> bool:
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "UPDATE socratic_prompts SET status='acknowledged', resolved_at=NOW() "
                "WHERE id=%s", (prompt_id,))
            ok = cur.rowcount > 0
        db_conn.commit()
        return ok
    except Exception as e:
        log.warning("mark_acknowledged failed: %s", e)
        return False
