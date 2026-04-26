"""Deterministic rules that produce Socratic prompts.

v1 rules (no Haiku — all deterministic):
  stale_hypothesis        — Hypothesis >7d, no linked Decision
  dormant_decision        — Decision >30d untouched, not superseded
  built_not_deployed      — Feature built, no deploy in 48h
  deploy_failure_streak   — 3+ failed deploys in 24h same project
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)


@dataclass
class SocraticPrompt:
    tenant_id: str
    project_id: str | None
    rule_name: str
    subject_kind: str
    subject_id: str | None
    question: str
    rationale: str = ""
    priority: int = 50


def scan_tenant(
    tenant_id: str, graph: Any = None, db_conn: Any = None,
) -> list[SocraticPrompt]:
    if graph is None:
        from nexus import overwatch_graph
        graph = overwatch_graph
    prompts: list[SocraticPrompt] = []
    for fn in (_stale_hypothesis, _dormant_decision,
               _built_not_deployed, _deploy_failure_streak):
        try:
            prompts.extend(fn(tenant_id, graph, db_conn))
        except Exception as e:
            log.warning("%s failed tenant=%s: %s", fn.__name__, tenant_id, e)
    log.info("scan_tenant %s: %d prompts", tenant_id[:12], len(prompts))
    return prompts


def _stale_hypothesis(tid, graph, db_conn):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rows = graph.query(
        "MATCH (h:Hypothesis {tenant_id: $tid}) WHERE h.updated_at < $cutoff "
        "OPTIONAL MATCH (h)-[r]->(:Decision) "
        "WITH h, count(r) AS linked WHERE linked = 0 "
        "RETURN h.id AS id, h.statement AS stmt, h.project_id AS pid LIMIT 10",
        {"tid": tid, "cutoff": cutoff},
    ) or []
    return [
        SocraticPrompt(
            tenant_id=tid, project_id=r.get("pid"),
            rule_name="stale_hypothesis", subject_kind="hypothesis",
            subject_id=r.get("id"),
            question=f"You proposed '{_trunc(r.get('stmt',''),80)}' over a week ago. Want to design an experiment?",
            rationale="Hypothesis not linked to any Decision after 7 days.",
            priority=60,
        ) for r in rows if r.get("stmt")
    ]


def _dormant_decision(tid, graph, db_conn):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = graph.query(
        "MATCH (d:Decision {tenant_id: $tid}) WHERE d.updated_at < $cutoff "
        "OPTIONAL MATCH (d)-[r:supersedes]->(:Decision) "
        "WITH d, count(r) AS sups WHERE sups = 0 "
        "RETURN d.id AS id, d.name AS name, d.updated_at AS upd, d.project_id AS pid LIMIT 5",
        {"tid": tid, "cutoff": cutoff},
    ) or []
    return [
        SocraticPrompt(
            tenant_id=tid, project_id=r.get("pid"),
            rule_name="dormant_decision", subject_kind="decision",
            subject_id=r.get("id"),
            question=f"You decided '{_trunc(r.get('name',''),80)}' on {_fmtdate(r.get('upd',''))}. Still right?",
            rationale="Decision untouched for 30+ days.", priority=40,
        ) for r in rows if r.get("name")
    ]


def _built_not_deployed(tid, graph, db_conn):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    rows = graph.query(
        "MATCH (f:Feature {tenant_id: $tid}) WHERE f.status IN ['built','ready','done'] "
        "AND f.updated_at < $cutoff "
        "RETURN f.id AS id, f.name AS name, f.project_id AS pid LIMIT 10",
        {"tid": tid, "cutoff": cutoff},
    ) or []
    if not rows or not db_conn:
        return []
    pids = {r.get("pid") for r in rows if r.get("pid")}
    deployed = _recent_success_pids(tid, pids, db_conn)
    return [
        SocraticPrompt(
            tenant_id=tid, project_id=r.get("pid"),
            rule_name="built_not_deployed", subject_kind="feature",
            subject_id=r.get("id"),
            question=f"You built '{_trunc(r.get('name',''),80)}' but it hasn't shipped in 48h. Stuck?",
            rationale="Feature built/ready, no recent deploy.", priority=70,
        ) for r in rows if r.get("name") and r.get("pid") not in deployed
    ]


def _deploy_failure_streak(tid, graph, db_conn):
    # Per migration 012 the source_kind column exists, so this query no
    # longer raises "column does not exist". But no producer writes
    # source_kind='deploy_event' rows yet — mechanism2 is unbuilt as of
    # 2026-04-26 (nexus/mechanism2/ is an empty directory). The rule
    # therefore returns zero rows in production today. The warning below
    # makes the missing-producer condition observable in CloudWatch
    # instead of silently dead. See docs/SILENT_EXCEPT_SWEEP_2026_04_26.md.
    # Real DB errors propagate to scan_tenant()'s outer try/except above.
    if not db_conn:
        return []
    log.warning(
        "deploy_failure_streak rule executed but no deploy_event producer "
        "exists; mechanism2 is unbuilt. See "
        "docs/SILENT_EXCEPT_SWEEP_2026_04_26.md."
    )
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT project_id, COUNT(*) AS cnt FROM classifier_proposals "
            "WHERE tenant_id=%s AND source_kind='deploy_event' "
            "AND raw_candidate::text LIKE %s AND created_at>%s "
            "GROUP BY project_id HAVING COUNT(*)>=3",
            (tid, "%deploy_failed%", cutoff))
        rows = cur.fetchall()
    return [
        SocraticPrompt(
            tenant_id=tid, project_id=pid,
            rule_name="deploy_failure_streak", subject_kind="project",
            subject_id=pid,
            question=f"Deploys to {pid} failed {cnt} times in 24h. Want to pair on diagnosis?",
            rationale=f"{cnt} deploy failures in 24h.", priority=85,
        ) for pid, cnt in rows
    ]


def _recent_success_pids(tid, pids, conn):
    # Same missing-producer condition as _deploy_failure_streak — no
    # producer writes source_kind='deploy_event' rows yet (mechanism2
    # unbuilt). Post-migration-012 this query no longer raises; it returns
    # an empty set until a producer ships. Real DB errors propagate to
    # scan_tenant()'s outer try/except.
    pids = [p for p in pids if p]
    if not pids:
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT project_id FROM classifier_proposals "
            "WHERE tenant_id=%s AND source_kind='deploy_event' "
            "AND raw_candidate::text LIKE %s AND project_id=ANY(%s) AND created_at>%s",
            (tid, "%deploy_succeeded%", pids, cutoff))
        return {r[0] for r in cur.fetchall()}


def _trunc(s, n):
    s = s.strip()
    return s if len(s) <= n else s[:n-1].rstrip() + "…"


def _fmtdate(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %d")
    except Exception:
        return "recently"
