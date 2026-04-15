"""
Onboarding Funnel Monitor — catch stuck tenants early.

Walks a 7-stage funnel per tenant (signup → repo → ingest → brief →
tasks → first PR → first merge). Each stage has a stall threshold; a
tenant sitting past threshold is surfaced in Tenant/Goal diagnosis
with a stage-specific hint.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import neptune_client
from nexus.config import MODE

logger = logging.getLogger("nexus.capabilities.onboarding_monitor")

# Ordered pipeline — each stage implies the previous ones completed.
STAGES = [
    "tenant_created", "repo_connected", "ingestion_complete",
    "brief_generated", "tasks_created", "first_pr", "first_merge",
]
STALL_THRESHOLDS_MIN: dict[str, int] = {
    "tenant_created": 60,
    "repo_connected": 60,
    "ingestion_complete": 30,
    "brief_generated": 15,
    "tasks_created": 240,
    "first_pr": 24 * 60,
    "first_merge": 48 * 60,
}
STAGE_HINTS: dict[str, str] = {
    "repo_connected": "User may be stuck on the GitHub App install screen.",
    "ingestion_complete": "RepoFile ingestion pipeline may be hung — check daemon.",
    "brief_generated": "Bedrock synthesis may be failing — check Bedrock errors.",
    "tasks_created": "Discovery chat may be incomplete — normal up to 4h.",
    "first_pr": "Daemon task execution slow — normal up to 24h.",
    "first_merge": "User may not have merged the first PR yet.",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _count(label: str, tenant_id: str, extra: str = "") -> int:
    if MODE != "production":
        return 0
    try:
        q = (f"MATCH (n:{label}) WHERE n.tenant_id = $tid {extra} "
             "RETURN count(n) AS cnt")
        rows = neptune_client.query(q, {"tid": tenant_id}) or []
        return int(rows[0].get("cnt", 0)) if rows else 0
    except Exception:
        logger.exception("onboarding count %s failed", label)
        return 0


def _latest_ts(label: str, tenant_id: str, field: str) -> datetime | None:
    if MODE != "production":
        return None
    try:
        q = (f"MATCH (n:{label}) WHERE n.tenant_id = $tid "
             f"AND n.{field} IS NOT NULL "
             f"RETURN n.{field} AS ts ORDER BY n.{field} DESC LIMIT 1")
        rows = neptune_client.query(q, {"tid": tenant_id}) or []
        return _parse_iso(rows[0].get("ts")) if rows else None
    except Exception:
        return None


def _stages_complete(tenant_id: str, ctx: dict[str, Any]) -> tuple[list[str], datetime | None]:
    """Return (done_stages, timestamp_of_last_done_stage)."""
    done: list[str] = []
    last_ts: datetime | None = _parse_iso(ctx.get("created_at"))
    if ctx.get("tenant_id") or ctx.get("created_at"):
        done.append("tenant_created")
    if (ctx.get("repo_url") or "").strip():
        done.append("repo_connected")
    if _count("RepoFile", tenant_id) >= 5:
        done.append("ingestion_complete")
        ts = _latest_ts("RepoFile", tenant_id, "created_at")
        if ts: last_ts = ts
    if _count("MissionBrief", tenant_id) > 0:
        done.append("brief_generated")
        ts = _latest_ts("MissionBrief", tenant_id, "synthesized_at") \
            or _latest_ts("MissionBrief", tenant_id, "created_at")
        if ts: last_ts = ts
    if _count("MissionTask", tenant_id) > 0:
        done.append("tasks_created")
        ts = _latest_ts("MissionTask", tenant_id, "created_at")
        if ts: last_ts = ts
    if _count("MissionTask", tenant_id,
              "AND n.pr_url IS NOT NULL") > 0:
        done.append("first_pr")
    if _count("MissionTask", tenant_id,
              "AND n.status = 'complete'") > 0:
        done.append("first_merge")
    return done, last_ts


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    h, m = divmod(int(seconds // 60), 60)
    return f"{h}h {m}m"


def get_onboarding_status(tenant_id: str) -> dict[str, Any]:
    """Return the funnel status for one tenant."""
    if MODE != "production":
        return {
            "tenant_id": tenant_id, "mock": True,
            "current_stage": "brief_generated",
            "stages_complete": ["tenant_created", "repo_connected",
                                 "ingestion_complete", "brief_generated"],
            "stages_pending": ["tasks_created", "first_pr", "first_merge"],
            "time_in_current_stage": "5m",
            "stalled": False, "stall_threshold": "15m",
        }
    ctx = neptune_client.get_tenant_context(tenant_id) or {}
    done, last_ts = _stages_complete(tenant_id, ctx)
    pending = [s for s in STAGES if s not in done]
    current = pending[0] if pending else done[-1]
    threshold_min = STALL_THRESHOLDS_MIN.get(current, 60)
    age_sec = (_now() - last_ts).total_seconds() if last_ts else 0
    stalled = bool(pending) and age_sec > threshold_min * 60
    return {
        "tenant_id": tenant_id,
        "current_stage": current,
        "stages_complete": done,
        "stages_pending": pending,
        "time_in_current_stage": _fmt_duration(age_sec) if last_ts else "?",
        "stalled": stalled,
        "stall_threshold": f"{threshold_min}m",
        "hint": STAGE_HINTS.get(current) if stalled else None,
    }


def scan_all_tenants() -> dict[str, Any]:
    """Compute the funnel for every real tenant; flag stalled ones."""
    if MODE != "production":
        return {"tenants": [], "stalled_count": 0, "mock": True}
    try:
        tenant_ids = neptune_client.get_tenant_ids() or []
    except Exception:
        logger.exception("onboarding scan: get_tenant_ids failed")
        return {"error": "tenant enumeration failed"}
    rows = [get_onboarding_status(tid) for tid in tenant_ids]
    stalled = [r for r in rows if r.get("stalled")]
    return {
        "tenants": rows,
        "stalled_count": len(stalled),
        "stalled": stalled,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def format_for_report(report: dict[str, Any] | None = None) -> str:
    """Markdown summary for Goal/Tenant diagnosis."""
    r = report if report is not None else scan_all_tenants()
    if r.get("error"):
        return f"## Onboarding\n_Unavailable: {r['error']}_"
    stalled = r.get("stalled") or []
    if not stalled:
        total = len(r.get("tenants") or [])
        return f"## Onboarding\n_{total} tenants; none stalled._"
    lines = [f"## Onboarding\n**{len(stalled)} tenant(s) stalled:**"]
    for s in stalled[:5]:
        lines.append(f"- {s['tenant_id'][:16]} at `{s['current_stage']}` for "
                     f"{s['time_in_current_stage']} "
                     f"(threshold {s['stall_threshold']}) — {s.get('hint') or '—'}")
    return "\n".join(lines)
