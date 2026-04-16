"""
Tenant Deep Dive — a single structured report aggregating activity,
engagement, pipeline health, intelligence depth, and recommendations
for one tenant. Powers the dashboard's per-tenant detail panel.

Read-only. Uses Neptune (via `nexus.neptune_client`) for all data;
every query is tenant-scoped. Results are cached per-tenant for 5 min.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import neptune_client
from nexus.config import MODE

logger = logging.getLogger(__name__)

_CACHE_TTL = 5 * 60
_cache: dict[str, tuple[dict[str, Any], float]] = {}

# Accretion Core sources Overwatch tracks for "intelligence depth."
_ACCRETION_LABELS = (
    "MissionBrief", "BriefEntry", "AnalysisReport", "RepoFile",
    "ConventionRule", "TrajectoryInsight", "IntentSnapshot",
    "UserPortrait", "ArchitectureSummary", "TimelineEvent",
    "DecisionRecord", "CapabilityMap",
)


def get_tenant_dive(tenant_id: str, force: bool = False) -> dict[str, Any]:
    """Main entry. Returns the full deep-dive report, cached per-tenant."""
    now = time.time()
    if not force and tenant_id in _cache:
        data, ts = _cache[tenant_id]
        if (now - ts) < _CACHE_TTL:
            return data

    result = {
        "tenant_id": tenant_id,
        "generated_at": _utcnow_iso(),
        "product": _product(tenant_id),
        "activity_timeline": _activity_timeline(tenant_id),
        "engagement": _engagement(tenant_id),
        "pipeline": _pipeline(tenant_id),
        "intelligence_depth": _intelligence_depth(tenant_id),
        "deploy_error": _deploy_error(tenant_id),
    }
    result["risk_signals"] = _risk_signals(result)
    result["recommendations"] = _recommendations(result)
    _cache[tenant_id] = (result, now)
    return result


def _product(tid: str) -> dict[str, Any]:
    """
    Product summary: name + what's being built + stage. Reads the active
    Project for the name and the MissionBrief for the prose product_summary.
    """
    name = ""
    repo_url = ""
    proj_rows = neptune_client.query(
        "MATCH (p:Project {tenant_id: $tid, status: 'active'}) "
        "RETURN p.name AS name, p.repo_url AS repo_url, "
        "p.project_id AS project_id LIMIT 1",
        {"tid": tid},
    ) or []
    if proj_rows and isinstance(proj_rows[0], dict):
        name = proj_rows[0].get("name") or ""
        repo_url = proj_rows[0].get("repo_url") or ""

    # MissionBrief carries the prose summary of what the product does.
    brief_rows = neptune_client.query(
        "MATCH (b:MissionBrief {tenant_id: $tid}) "
        "RETURN b.product_summary AS summary, b.updated_at AS updated_at "
        "ORDER BY b.updated_at DESC LIMIT 1",
        {"tid": tid},
    ) or []
    summary = ""
    if brief_rows and isinstance(brief_rows[0], dict):
        summary = (brief_rows[0].get("summary") or "").strip()

    # Stage lives on the Tenant node (mission_stage).
    stage_rows = neptune_client.query(
        "MATCH (t:Tenant {tenant_id: $tid}) RETURN t.mission_stage AS stage LIMIT 1",
        {"tid": tid},
    ) or []
    stage = (stage_rows[0].get("stage") if stage_rows and isinstance(stage_rows[0], dict) else "") or ""

    # First ~240 chars of the summary keeps the panel scannable.
    short_summary = summary[:240] + ("…" if len(summary) > 240 else "")

    return {
        "name": name,
        "repo_url": repo_url,
        "summary": short_summary,
        "summary_full": summary,
        "stage": stage,
    }


def _deploy_error(tid: str) -> dict[str, Any] | None:
    """
    When the latest DeploymentProgress is in a failed state, surface the
    crash details (message, last 10 container log lines, diagnosis root
    cause + suggested fix). Returns None when no failed deploy exists.
    """
    rows = neptune_client.query(
        "MATCH (d:DeploymentProgress {tenant_id: $tid}) "
        "RETURN d.stage AS stage, d.message AS message, "
        "d.container_log AS container_log, d.diagnosis_json AS diagnosis, "
        "d.updated_at AS updated_at "
        "ORDER BY d.updated_at DESC LIMIT 1",
        {"tid": tid},
    ) or []
    if not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    stage = (row.get("stage") or "").lower()
    if stage not in ("failed", "error"):
        return None

    log = (row.get("container_log") or "").strip()
    log_tail = "\n".join(log.splitlines()[-10:]) if log else ""

    root_cause = ""
    suggested_fix = ""
    diag_raw = row.get("diagnosis") or ""
    if diag_raw:
        try:
            import json as _json
            diag = _json.loads(diag_raw)
            if isinstance(diag, dict):
                root_cause = (diag.get("root_cause") or "")[:500]
                steps = diag.get("user_steps") or []
                if steps and isinstance(steps, list):
                    first = steps[0]
                    if isinstance(first, dict):
                        suggested_fix = (first.get("step") or "")[:300]
                    elif isinstance(first, str):
                        suggested_fix = first[:300]
        except (ValueError, TypeError):
            pass

    failed_at = _parse(row.get("updated_at"))
    return {
        "stage": stage,
        "message": (row.get("message") or "").strip()[:500],
        "log_tail": log_tail,
        "log_line_count": len(log.splitlines()) if log else 0,
        "root_cause": root_cause,
        "suggested_fix": suggested_fix,
        "failed_at": failed_at.isoformat() if failed_at else None,
        "failed_since": _since(failed_at),
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _since(dt: datetime | None) -> str:
    if not dt:
        return "unknown"
    delta = datetime.now(timezone.utc) - dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)}m ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


def _activity_timeline(tid: str, hours: int = 48) -> list[dict[str, Any]]:
    """Last 48h of notable events across messages, tasks, briefs, deploys."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    events: list[dict[str, Any]] = []

    def _fetch(cypher: str, kind: str, summarise) -> None:
        for r in neptune_client.query(cypher, {"tid": tid, "cutoff": cutoff}) or []:
            if not isinstance(r, dict):
                continue
            dt = _parse(r.get("ts"))
            if not dt:
                continue
            events.append({"kind": kind, "at": dt.isoformat(),
                           "since": _since(dt), "summary": summarise(r)})

    _fetch(
        "MATCH (m:ConversationMessage {tenant_id: $tid}) "
        "WHERE m.timestamp >= $cutoff "
        "RETURN m.role AS role, substring(coalesce(m.content,''),0,80) AS content, "
        "m.timestamp AS ts ORDER BY m.timestamp DESC LIMIT 30",
        "message",
        lambda r: f"{r.get('role','user')}: {r.get('content','')}",
    )
    _fetch(
        "MATCH (t:MissionTask {tenant_id: $tid}) "
        "WHERE coalesce(t.updated_at, t.created_at) >= $cutoff "
        "RETURN t.status AS status, substring(coalesce(t.description,''),0,60) AS desc, "
        "t.pr_number AS pr, coalesce(t.updated_at, t.created_at) AS ts LIMIT 30",
        "task",
        lambda r: (f"Task → {r.get('status','?')}"
                   + (f" (PR #{r.get('pr')})" if r.get("pr") else "")
                   + (f": {r['desc']}" if r.get("desc") else "")),
    )
    _fetch(
        "MATCH (b:BriefEntry {tenant_id: $tid}) WHERE b.created_at >= $cutoff "
        "RETURN b.entry_type AS etype, substring(coalesce(b.summary,''),0,60) AS summary, "
        "b.created_at AS ts LIMIT 15",
        "brief",
        lambda r: f"Brief {r.get('etype','updated')}: {r.get('summary','')}",
    )
    _fetch(
        "MATCH (d:DeploymentProgress {tenant_id: $tid}) WHERE d.updated_at >= $cutoff "
        "RETURN d.stage AS stage, substring(coalesce(d.message,''),0,60) AS message, "
        "d.updated_at AS ts LIMIT 10",
        "deploy",
        lambda r: f"Deploy [{r.get('stage','?')}]: {r.get('message','')}",
    )

    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:40]


def _engagement(tid: str) -> dict[str, Any]:
    rows = neptune_client.query(
        "MATCH (m:ConversationMessage {tenant_id: $tid, role: 'user'}) "
        "RETURN m.timestamp AS ts, length(coalesce(m.content,'')) AS len "
        "ORDER BY m.timestamp DESC LIMIT 500",
        {"tid": tid},
    ) or []
    timestamps = [dt for r in rows if isinstance(r, dict)
                  for dt in [_parse(r.get("ts"))] if dt]
    lengths = [int(r.get("len") or 0) for r in rows if isinstance(r, dict)]
    now = datetime.now(timezone.utc)
    last_7d = [t for t in timestamps if (now - t).days < 7]
    prior_7d = [t for t in timestamps if 7 <= (now - t).days < 14]
    trend = "stable"
    if len(last_7d) > len(prior_7d) * 1.3 and len(last_7d) > 3:
        trend = "rising"
    elif len(prior_7d) > len(last_7d) * 1.3 and len(prior_7d) > 3:
        trend = "falling"

    hours = [t.hour for t in last_7d]
    session = "unknown"
    if hours:
        avg = sum(hours) / len(hours)
        session = "morning" if avg < 12 else "afternoon" if avg < 17 else "evening"

    avg_len = (sum(lengths[:20]) / len(lengths[:20])) if lengths[:20] else 0
    early_avg = (sum(lengths[20:40]) / len(lengths[20:40])) if lengths[20:40] else avg_len
    sentiment = ("confused" if avg_len > early_avg * 1.4 and avg_len > 100
                 else "comfortable" if avg_len and avg_len < early_avg * 0.7
                 else "steady")

    review_hours = _avg_review_hours(tid)
    score = _engagement_score(len(last_7d), review_hours, trend)

    return {
        "last_active": timestamps[0].isoformat() if timestamps else None,
        "last_active_since": _since(timestamps[0] if timestamps else None),
        "activity_trend": trend,
        "avg_review_hours": round(review_hours, 1) if review_hours else None,
        "engagement_score": score,
        "session_pattern": session,
        "messages_last_7d": len(last_7d),
        "conversation_sentiment": sentiment,
    }


def _avg_review_hours(tid: str) -> float:
    rows = neptune_client.query(
        "MATCH (t:MissionTask {tenant_id: $tid}) "
        "WHERE t.pr_opened_at IS NOT NULL AND t.pr_merged_at IS NOT NULL "
        "RETURN t.pr_opened_at AS opened, t.pr_merged_at AS merged LIMIT 50",
        {"tid": tid},
    ) or []
    deltas: list[float] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        o, m = _parse(r.get("opened")), _parse(r.get("merged"))
        if o and m and m > o:
            deltas.append((m - o).total_seconds() / 3600)
    return sum(deltas) / len(deltas) if deltas else 0.0


def _engagement_score(msg_7d: int, review_hours: float, trend: str) -> int:
    msg_component = min(msg_7d * 3, 50)
    review_component = 0 if not review_hours else max(0, 30 - int(review_hours))
    trend_bonus = {"rising": 20, "stable": 10, "falling": 0}.get(trend, 0)
    return max(0, min(100, msg_component + review_component + trend_bonus))


def _pipeline(tid: str) -> dict[str, Any]:
    tasks = neptune_client.query(
        "MATCH (t:MissionTask {tenant_id: $tid}) "
        "RETURN t.status AS status, t.created_at AS created_at, "
        "t.pr_opened_at AS pr_opened, t.pr_merged_at AS pr_merged, "
        "t.pr_state AS pr_state LIMIT 500",
        {"tid": tid},
    ) or []
    counts = {"total": 0, "pending": 0, "in_review": 0, "complete": 0, "shelved": 0}
    pr_open = pr_merged = 0
    merge_times: list[datetime] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        counts["total"] += 1
        s = (t.get("status") or "").lower()
        if s in counts:
            counts[s] += 1
        if (t.get("pr_state") or "").lower() == "open":
            pr_open += 1
        if t.get("pr_merged"):
            pr_merged += 1
            m = _parse(t.get("pr_merged"))
            if m:
                merge_times.append(m)

    now = datetime.now(timezone.utc)
    recent_merges = [m for m in merge_times if (now - m).days < 7]
    velocity = round(len(recent_merges) / 7, 2)

    deploy = neptune_client.query(
        "MATCH (d:DeploymentProgress {tenant_id: $tid}) "
        "RETURN d.stage AS stage, d.updated_at AS updated_at "
        "ORDER BY d.updated_at DESC LIMIT 1",
        {"tid": tid},
    ) or []
    deploy_stage = (deploy[0].get("stage") if deploy else "not_provisioned") or "not_provisioned"

    brief = neptune_client.query(
        "MATCH (b:BriefEntry {tenant_id: $tid}) "
        "RETURN b.created_at AS ts ORDER BY b.created_at DESC LIMIT 1",
        {"tid": tid},
    ) or []
    brief_dt = _parse(brief[0].get("ts")) if brief else None

    return {
        "tasks_total": counts["total"],
        "tasks_pending": counts["pending"],
        "tasks_in_review": counts["in_review"],
        "tasks_complete": counts["complete"],
        "tasks_shelved": counts["shelved"],
        "prs_open": pr_open,
        "prs_merged": pr_merged,
        "pr_velocity_per_day": velocity,
        "deploy_status": deploy_stage,
        "brief_freshness": _since(brief_dt),
        "brief_last_updated": brief_dt.isoformat() if brief_dt else None,
    }


def _intelligence_depth(tid: str) -> dict[str, Any]:
    populated: list[str] = []
    for label in _ACCRETION_LABELS:
        rows = neptune_client.query(
            f"MATCH (n:{label} {{tenant_id: $tid}}) RETURN count(n) AS c LIMIT 1",
            {"tid": tid},
        ) or []
        c = (rows[0].get("c") if rows and isinstance(rows[0], dict) else 0) or 0
        if c:
            populated.append(label)

    def _one(label: str) -> int:
        rows = neptune_client.query(
            f"MATCH (n:{label} {{tenant_id: $tid}}) RETURN count(n) AS c",
            {"tid": tid},
        ) or []
        return int((rows[0].get("c") if rows and isinstance(rows[0], dict) else 0) or 0)

    report_rows = neptune_client.query(
        "MATCH (r:AnalysisReport {tenant_id: $tid}) "
        "RETURN DISTINCT r.report_type AS rt",
        {"tid": tid},
    ) or []
    report_types = sorted({r["rt"] for r in report_rows
                           if isinstance(r, dict) and r.get("rt")})

    return {
        "sources_populated": len(populated),
        "sources_total": len(_ACCRETION_LABELS),
        "populated_labels": populated,
        "convention_rules": _one("ConventionRule"),
        "trajectory_exists": "TrajectoryInsight" in populated,
        "intent_exists": "IntentSnapshot" in populated,
        "portrait_exists": "UserPortrait" in populated,
        "analysis_reports": report_types,
    }


def _risk_signals(data: dict[str, Any]) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    pipe = data["pipeline"]
    eng = data["engagement"]

    if pipe["prs_open"] >= 3:
        signals.append({"severity": "warning",
                        "signal": f"{pipe['prs_open']} PRs awaiting review — pileup risk"})
    elif pipe["prs_open"] >= 1:
        signals.append({"severity": "info",
                        "signal": f"{pipe['prs_open']} PR(s) awaiting review"})

    brief_dt = _parse(data["pipeline"]["brief_last_updated"])
    if brief_dt and (datetime.now(timezone.utc) - brief_dt).days > 7:
        signals.append({"severity": "warning",
                        "signal": f"Brief is {(datetime.now(timezone.utc) - brief_dt).days}d stale"})

    last_active = _parse(eng["last_active"])
    if last_active and (datetime.now(timezone.utc) - last_active).total_seconds() > 48 * 3600 \
            and pipe["prs_open"]:
        signals.append({"severity": "warning",
                        "signal": "User idle >48h with open PRs — may be disengaged"})

    if pipe["deploy_status"] in ("failed", "error"):
        signals.append({"severity": "critical",
                        "signal": f"Deploy in {pipe['deploy_status']} state"})

    return signals


def _recommendations(data: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    pipe = data["pipeline"]
    eng = data["engagement"]
    risks = data["risk_signals"]
    product = data.get("product") or {}
    prefix = f"{product.get('name')} — " if product.get("name") else ""

    critical = [r for r in risks if r["severity"] == "critical"]
    if critical:
        recs.extend(f"{prefix}Investigate: {r['signal']}" for r in critical[:2])
        return recs

    if pipe["prs_open"] >= 3:
        recs.append(f"{prefix}Nudge user — {pipe['prs_open']} PRs have been sitting.")
    if any("brief" in r["signal"].lower() for r in risks):
        recs.append(f"{prefix}Brief is stale — Accretion quality may be degrading; consider re-synthesis.")

    if eng["messages_last_7d"] >= 10 and pipe["prs_merged"] > pipe["prs_open"]:
        recs.append(f"{prefix}Tenant is in flow state — highly engaged, pipeline healthy. No action needed.")
    elif eng["activity_trend"] == "falling" and eng["messages_last_7d"] < 3:
        recs.append(f"{prefix}Activity trending down — consider outreach before this tenant goes cold.")

    if not recs:
        recs.append("No action needed.")
    return recs[:3]


def clear_cache(tenant_id: str | None = None) -> None:
    """Clear the cache (entire, or for one tenant)."""
    if tenant_id is None:
        _cache.clear()
    else:
        _cache.pop(tenant_id, None)
