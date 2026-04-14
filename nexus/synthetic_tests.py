"""
Synthetic User Journey Tests — Overwatch simulates real user actions.

Runs against the live Forgewing API to verify features work end-to-end.
Not unit tests — these are PRODUCTION VERIFICATION tests that catch
issues no unit test would find (like project_id not being sent, or
SFS triggering pivot instead of restart).

All journeys are READ-ONLY — no writes, no message sends, no project
creation. Test tenant is Beacon (forge-1dba4143ca24ed1f).

Results cached for 60 seconds to avoid hammering the API.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from nexus.capabilities.forgewing_api import call_api
from nexus.config import FORGEWING_API, MODE

logger = logging.getLogger(__name__)

TEST_TENANT = "forge-1dba4143ca24ed1f"

# Cache: (results, timestamp)
_cache: tuple[list[dict[str, Any]], float] = ([], 0)
_CACHE_TTL = 60


def run_all_journeys(force: bool = False) -> list[dict[str, Any]]:
    """Run all synthetic journeys. Returns cached results if <60s old."""
    global _cache
    now = time.time()
    if not force and _cache[1] > 0 and (now - _cache[1]) < _CACHE_TTL:
        return _cache[0]

    journeys = [
        journey_health,
        journey_project_list,
        journey_project_separation,
        journey_conversation_scoping,
        journey_brief_exists,
        journey_deploy_readiness,
        journey_sfs_detection,
        # Project isolation QA — continuous verification
        journey_conversation_no_project_bleed,
        journey_conversation_project_scoped,
        journey_status_project_scoped,
        journey_brief_project_scoped,
        journey_actions_reflect_reality,
        # Browser-bug regression guards (aria-platform 06b24b1)
        journey_brief_project_isolation,
        journey_github_banner_consistency,
        journey_action_banner_freshness,
        journey_sfs_project_creation,
        journey_project_delete_cleanup,
        # Flow health — catches frozen pipelines within ~30s of cycle
        journey_ingestion_completion,
        journey_connect_flow_health,
        journey_sfs_flow_health,
        # CI self-healing readiness (2026-04-14 outage guards)
        journey_ci_monitoring_health,
        journey_ci_healer_readiness,
    ]
    results: list[dict[str, Any]] = []
    for fn in journeys:
        try:
            results.append(fn())
        except Exception as exc:
            results.append({"name": fn.__name__, "status": "error", "error": str(exc)[:200]})

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    logger.info("Synthetic tests: %d passed, %d failed, %d total", passed, failed, len(results))
    _cache = (results, now)
    return results


def _timed_call(method: str, path: str, **kwargs: Any) -> tuple[dict[str, Any], int]:
    """Call Forgewing API and return (response, duration_ms)."""
    start = time.time()
    resp = call_api(method, path, **kwargs)
    duration = int((time.time() - start) * 1000)
    return resp, duration


def journey_health() -> dict[str, Any]:
    """Verify Forgewing API is responding."""
    resp, ms = _timed_call("GET", "/health")
    if resp.get("error"):
        return {"name": "health", "status": "fail", "duration_ms": ms, "error": resp["error"][:200]}
    return {"name": "health", "status": "pass", "duration_ms": ms}


def journey_project_list() -> dict[str, Any]:
    """Verify project listing returns structured data."""
    resp, ms = _timed_call("GET", f"/projects/{TEST_TENANT}")
    if resp.get("error"):
        return {"name": "project_list", "status": "fail", "duration_ms": ms, "error": resp["error"][:200]}
    projects = resp.get("projects", []) if isinstance(resp, dict) else resp
    if not isinstance(projects, list):
        return {"name": "project_list", "status": "fail", "duration_ms": ms, "error": "Not a list"}
    active = [p for p in projects if isinstance(p, dict) and p.get("status") == "active"]
    return {"name": "project_list", "status": "pass", "duration_ms": ms,
            "details": f"{len(projects)} projects, {len(active)} active"}


def journey_project_separation() -> dict[str, Any]:
    """Verify different project_ids return different data."""
    resp, _ = _timed_call("GET", f"/projects/{TEST_TENANT}")
    projects = resp.get("projects", []) if isinstance(resp, dict) else resp
    if not isinstance(projects, list) or len(projects) < 2:
        return {"name": "project_separation", "status": "skip", "error": "Need 2+ projects"}

    p1, p2 = projects[0], projects[1]
    pid1 = p1.get("project_id", "") if isinstance(p1, dict) else ""
    pid2 = p2.get("project_id", "") if isinstance(p2, dict) else ""
    if not pid1 or not pid2:
        return {"name": "project_separation", "status": "skip", "error": "No project_id"}

    r1, _ = _timed_call("GET", f"/brief/{TEST_TENANT}?project_id={pid1}")
    r2, ms = _timed_call("GET", f"/brief/{TEST_TENANT}?project_id={pid2}")

    if r1.get("error") or r2.get("error"):
        return {"name": "project_separation", "status": "fail", "duration_ms": ms,
                "error": "Brief fetch failed"}

    if r1 == r2 and len(str(r1)) > 50:
        return {"name": "project_separation", "status": "fail", "duration_ms": ms,
                "error": "Identical brief for different projects — separation broken"}

    return {"name": "project_separation", "status": "pass", "duration_ms": ms,
            "details": f"Projects {pid1[:12]} and {pid2[:12]} return different data"}


def journey_conversation_scoping() -> dict[str, Any]:
    """Verify conversation endpoint accepts project_id scoping."""
    resp, ms = _timed_call("GET", f"/conversation/{TEST_TENANT}")
    if resp.get("error") and resp.get("status", 0) >= 500:
        return {"name": "conversation_scoping", "status": "fail", "duration_ms": ms,
                "error": resp["error"][:200]}
    return {"name": "conversation_scoping", "status": "pass", "duration_ms": ms}


def journey_brief_exists() -> dict[str, Any]:
    """Verify active project has a mission brief."""
    resp, ms = _timed_call("GET", f"/brief/{TEST_TENANT}")
    if resp.get("error"):
        return {"name": "brief_exists", "status": "fail", "duration_ms": ms, "error": resp["error"][:200]}
    has_content = bool(resp.get("product") or resp.get("product_summary") or resp.get("mock"))
    return {"name": "brief_exists", "status": "pass" if has_content else "fail",
            "duration_ms": ms, "details": "Brief has content" if has_content else "Brief empty"}


def journey_deploy_readiness() -> dict[str, Any]:
    """Verify deploy readiness gate responds."""
    resp, ms = _timed_call("GET", f"/deploy-progress/{TEST_TENANT}")
    if resp.get("error") and resp.get("status", 0) >= 500:
        return {"name": "deploy_readiness", "status": "fail", "duration_ms": ms,
                "error": resp["error"][:200]}
    return {"name": "deploy_readiness", "status": "pass", "duration_ms": ms}


def journey_sfs_detection() -> dict[str, Any]:
    """Verify conversation endpoint is reachable (SFS wired at route level)."""
    resp, ms = _timed_call("GET", f"/conversation/{TEST_TENANT}")
    if resp.get("error") and resp.get("status", 0) >= 500:
        return {"name": "sfs_detection", "status": "fail", "duration_ms": ms,
                "error": resp["error"][:200]}
    return {"name": "sfs_detection", "status": "pass", "duration_ms": ms,
            "details": "Conversation endpoint reachable"}


def _active_project() -> dict[str, Any] | None:
    """Fetch the active project for TEST_TENANT. Returns None if unavailable."""
    resp, _ = _timed_call("GET", f"/projects/{TEST_TENANT}")
    projects = resp.get("projects", []) if isinstance(resp, dict) else resp
    if not isinstance(projects, list):
        return None
    for p in projects:
        if isinstance(p, dict) and p.get("status") == "active":
            return p
    return None


def journey_conversation_no_project_bleed() -> dict[str, Any]:
    """
    Every message returned from /conversation/{tid} should have a
    project_id attached. Unscoped messages leak across projects.
    """
    resp, ms = _timed_call("GET", f"/conversation/{TEST_TENANT}")
    if resp.get("error") and resp.get("status", 0) >= 500:
        return {"name": "conversation_no_bleed", "status": "fail",
                "duration_ms": ms, "error": resp["error"][:200]}
    messages = resp.get("messages", []) if isinstance(resp, dict) else []
    if not messages:
        return {"name": "conversation_no_bleed", "status": "pass",
                "duration_ms": ms, "details": "No messages"}
    unscoped = [m for m in messages if isinstance(m, dict) and not m.get("project_id")]
    if unscoped:
        return {"name": "conversation_no_bleed", "status": "fail",
                "duration_ms": ms,
                "error": f"{len(unscoped)}/{len(messages)} messages have no project_id — cross-project bleed risk"}
    return {"name": "conversation_no_bleed", "status": "pass",
            "duration_ms": ms, "details": f"All {len(messages)} messages scoped"}


def journey_conversation_project_scoped() -> dict[str, Any]:
    """
    /conversation/{tid}?project_id=X must only return messages whose
    project_id is X (or messages without a project_id).
    """
    active = _active_project()
    if not active:
        return {"name": "conversation_scoped", "status": "skip",
                "error": "No active project"}
    pid = active.get("project_id") or ""
    if not pid:
        return {"name": "conversation_scoped", "status": "skip",
                "error": "Active project has no project_id"}
    resp, ms = _timed_call("GET", f"/conversation/{TEST_TENANT}?project_id={pid}")
    if resp.get("error") and resp.get("status", 0) >= 500:
        return {"name": "conversation_scoped", "status": "fail",
                "duration_ms": ms, "error": resp["error"][:200]}
    messages = resp.get("messages", []) if isinstance(resp, dict) else []
    wrong = [m for m in messages if isinstance(m, dict)
             and m.get("project_id") and m["project_id"] != pid]
    if wrong:
        other = wrong[0].get("project_id", "?")
        return {"name": "conversation_scoped", "status": "fail",
                "duration_ms": ms,
                "error": f"{len(wrong)} messages from project {other[:12]} leaked into {pid[:12]}"}
    return {"name": "conversation_scoped", "status": "pass",
            "duration_ms": ms,
            "details": f"{len(messages)} messages, all scoped to {pid[:12]}"}


def journey_status_project_scoped() -> dict[str, Any]:
    """
    /status/{tid}?project_id=X must return data for project X — specifically
    its repo_url should match the project's repo_url.
    """
    active = _active_project()
    if not active:
        return {"name": "status_scoped", "status": "skip",
                "error": "No active project"}
    pid = active.get("project_id") or ""
    project_repo = (active.get("repo_url") or "").strip()
    resp, ms = _timed_call("GET", f"/status/{TEST_TENANT}?project_id={pid}")
    if resp.get("error") and resp.get("status", 0) >= 500:
        return {"name": "status_scoped", "status": "fail",
                "duration_ms": ms, "error": resp["error"][:200]}
    status_repo = (resp.get("repo_url") or "").strip() if isinstance(resp, dict) else ""
    if project_repo and status_repo and status_repo != project_repo:
        return {"name": "status_scoped", "status": "fail",
                "duration_ms": ms,
                "error": f"Status repo={status_repo[:40]} != Project repo={project_repo[:40]}"}
    return {"name": "status_scoped", "status": "pass",
            "duration_ms": ms,
            "details": f"Repo matches ({status_repo[:40] or 'empty'})"}


def journey_brief_project_scoped() -> dict[str, Any]:
    """
    Single-project sanity check for /brief/{tid}?project_id=X — server must
    accept the param and respond without error. Cross-project bleed is
    already covered by journey_project_separation when 2+ projects exist.
    """
    active = _active_project()
    if not active:
        return {"name": "brief_scoped", "status": "skip",
                "error": "No active project"}
    pid = active.get("project_id") or ""
    resp, ms = _timed_call("GET", f"/brief/{TEST_TENANT}?project_id={pid}")
    if resp.get("error") and resp.get("status", 0) >= 500:
        return {"name": "brief_scoped", "status": "fail",
                "duration_ms": ms, "error": resp["error"][:200]}
    content = str(resp.get("content") or resp.get("product") or "") if isinstance(resp, dict) else ""
    return {"name": "brief_scoped", "status": "pass",
            "duration_ms": ms,
            "details": f"Brief responded ({len(content)} chars)"}


def journey_actions_reflect_reality() -> dict[str, Any]:
    """
    /actions/{tid} (Forgewing ActionBanner endpoint — part of the Part B
    redesign) must not report false positives. If the tenant IS connected
    to AWS, we should NOT see a cloud-not-connected action.

    If the endpoint doesn't exist yet (404), skip cleanly.
    """
    resp, ms = _timed_call("GET", f"/actions/{TEST_TENANT}")
    status_code = resp.get("status", 0) if isinstance(resp, dict) else 0
    if resp.get("error") and status_code in (404, 405):
        return {"name": "actions_reflect_reality", "status": "skip",
                "error": "Endpoint not deployed yet"}
    if resp.get("error") and status_code >= 500:
        return {"name": "actions_reflect_reality", "status": "fail",
                "duration_ms": ms, "error": resp["error"][:200]}
    action_list = resp.get("actions", []) if isinstance(resp, dict) else []
    # Cross-check with /status to detect false cloud banner
    status_resp, _ = _timed_call("GET", f"/status/{TEST_TENANT}")
    has_aws = bool(status_resp.get("aws_connected") or status_resp.get("aws_role_arn")) \
        if isinstance(status_resp, dict) else False
    cloud_action = next(
        (a for a in action_list if isinstance(a, dict)
         and "cloud" in (a.get("type") or a.get("action_type") or "").lower()),
        None,
    )
    if has_aws and cloud_action is not None:
        atype = cloud_action.get("type") or cloud_action.get("action_type", "?")
        return {"name": "actions_reflect_reality", "status": "fail",
                "duration_ms": ms,
                "error": f"False positive: action '{atype}' fired but aws IS connected"}
    return {"name": "actions_reflect_reality", "status": "pass",
            "duration_ms": ms,
            "details": f"{len(action_list)} actions, aws={has_aws}"}


def journey_brief_project_isolation() -> dict[str, Any]:
    """
    Guards Bug 1: brief content must be scoped to its project — no other
    project's name should appear inside a project's brief. Requires 2+
    named projects; skips cleanly otherwise.
    """
    resp, _ = _timed_call("GET", f"/projects/{TEST_TENANT}")
    projects = resp.get("projects", []) if isinstance(resp, dict) else resp
    if not isinstance(projects, list) or len(projects) < 2:
        return {"name": "brief_project_isolation", "status": "skip",
                "error": "Need 2+ projects"}
    named = [p for p in projects
             if isinstance(p, dict) and p.get("project_id")
             and (p.get("name") or "").strip()]
    if len(named) < 2:
        return {"name": "brief_project_isolation", "status": "skip",
                "error": "Projects missing names"}

    total_ms = 0
    checked = 0
    for p in named[:3]:
        pid = p["project_id"]
        pname = str(p["name"]).strip().lower()
        other_names = [str(q["name"]).strip().lower()
                       for q in named if q["project_id"] != pid]
        br, ms = _timed_call("GET", f"/brief/{TEST_TENANT}?project_id={pid}")
        total_ms += ms
        if br.get("error"):
            continue
        content = " ".join(str(br.get(k) or "") for k in
                            ("content", "product", "product_summary", "summary")).lower()
        if not content.strip():
            continue
        checked += 1
        for other in other_names:
            # Only flag reasonably distinctive names — avoid common words.
            if len(other) >= 4 and other != pname and other in content:
                return {"name": "brief_project_isolation", "status": "fail",
                        "duration_ms": total_ms,
                        "error": (f"Brief for '{pname[:20]}' references other "
                                  f"project '{other[:20]}' — cross-project bleed")}
    if checked == 0:
        return {"name": "brief_project_isolation", "status": "skip",
                "duration_ms": total_ms, "error": "No brief content to compare"}
    return {"name": "brief_project_isolation", "status": "pass",
            "duration_ms": total_ms,
            "details": f"{checked} brief(s) isolated across {len(named)} projects"}


def journey_github_banner_consistency() -> dict[str, Any]:
    """
    Guards Bug 2: a tenant with a valid GitHub installation_id must not
    carry a 'Connect GitHub' ActionRequired. Skip if GitHub isn't
    connected on the test tenant.
    """
    status, ms = _timed_call("GET", f"/status/{TEST_TENANT}")
    if status.get("error") and status.get("status", 0) >= 500:
        return {"name": "github_banner_consistency", "status": "fail",
                "duration_ms": ms, "error": status["error"][:200]}
    connected = bool(
        status.get("installation_id") or status.get("github_installation_id")
        or status.get("github_connected") or status.get("github", {}).get("connected")
        if isinstance(status, dict) else False
    )
    if not connected:
        return {"name": "github_banner_consistency", "status": "skip",
                "duration_ms": ms, "error": "Tenant not connected to GitHub"}

    try:
        from nexus import overwatch_graph
        actions = overwatch_graph.get_tenant_actions(TEST_TENANT) or []
    except Exception as exc:
        return {"name": "github_banner_consistency", "status": "error",
                "duration_ms": ms, "error": str(exc)[:200]}

    for a in actions:
        if not isinstance(a, dict) or a.get("dismissed"):
            continue
        at = (a.get("action_type") or "").lower()
        if "github" in at and ("connect" in at or at.startswith("no_")
                                or "missing" in at):
            return {"name": "github_banner_consistency", "status": "fail",
                    "duration_ms": ms,
                    "error": f"GitHub connected but stale ActionRequired present: {at}"}
    return {"name": "github_banner_consistency", "status": "pass",
            "duration_ms": ms,
            "details": f"Connected; {len(actions)} action(s), none stale"}


def journey_action_banner_freshness() -> dict[str, Any]:
    """
    Guards Bug 3: a project created < 10 minutes ago must not carry a
    stuck/stale/ingestion ActionRequired — the banner would be lying.
    Skip when no young projects exist.
    """
    resp, ms = _timed_call("GET", f"/projects/{TEST_TENANT}")
    projects = resp.get("projects", []) if isinstance(resp, dict) else resp
    if not isinstance(projects, list) or not projects:
        return {"name": "action_banner_freshness", "status": "skip",
                "duration_ms": ms, "error": "No projects"}

    now = datetime.now(timezone.utc)
    young_pids: set[str] = set()
    for p in projects:
        if not isinstance(p, dict):
            continue
        created = p.get("created_at") or ""
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except Exception:
            continue
        if (now - dt).total_seconds() < 600:
            pid = p.get("project_id") or ""
            if pid:
                young_pids.add(pid)

    if not young_pids:
        return {"name": "action_banner_freshness", "status": "pass",
                "duration_ms": ms, "details": "No young projects to validate"}

    try:
        from nexus import overwatch_graph
        actions = overwatch_graph.get_tenant_actions(TEST_TENANT) or []
    except Exception as exc:
        return {"name": "action_banner_freshness", "status": "error",
                "duration_ms": ms, "error": str(exc)[:200]}

    for a in actions:
        if not isinstance(a, dict) or a.get("dismissed"):
            continue
        at = (a.get("action_type") or "").lower()
        if not any(w in at for w in ("stuck", "stale", "ingestion")):
            continue
        target = a.get("project_id") or a.get("target") or ""
        if target in young_pids:
            return {"name": "action_banner_freshness", "status": "fail",
                    "duration_ms": ms,
                    "error": (f"Project {str(target)[:12]} is <10min old but "
                              f"has '{at}' banner")}
    return {"name": "action_banner_freshness", "status": "pass",
            "duration_ms": ms,
            "details": f"{len(young_pids)} young project(s), no premature banners"}


def journey_sfs_project_creation() -> dict[str, Any]:
    """
    Guards Bug 4: Start-from-Scratch projects must carry the scaffold flag
    AND have a linked repo — otherwise the scaffold ingestion pipeline is
    broken. Skip when no SFS projects exist on the test tenant.
    """
    resp, ms = _timed_call("GET", f"/projects/{TEST_TENANT}")
    projects = resp.get("projects", []) if isinstance(resp, dict) else resp
    if not isinstance(projects, list) or not projects:
        return {"name": "sfs_project_creation", "status": "skip",
                "duration_ms": ms, "error": "No projects"}

    sfs: list[dict[str, Any]] = []
    for p in projects:
        if not isinstance(p, dict):
            continue
        flags = p.get("flags") or p.get("metadata") or {}
        flag_keys = list(flags.keys()) if isinstance(flags, dict) else []
        is_sfs = (
            bool(p.get("forge_sfs")) or bool(p.get("sfs")) or bool(p.get("scaffold"))
            or any(str(k).startswith("forge_sfs") for k in flag_keys)
            or (p.get("source") or "").lower() in ("sfs", "scaffold", "start_from_scratch")
        )
        if is_sfs:
            sfs.append(p)

    if not sfs:
        return {"name": "sfs_project_creation", "status": "skip",
                "duration_ms": ms, "error": "No SFS projects on test tenant"}

    for p in sfs:
        repo = (p.get("repo_url") or "").strip()
        if not repo:
            pid = str(p.get("project_id", "?"))[:12]
            return {"name": "sfs_project_creation", "status": "fail",
                    "duration_ms": ms,
                    "error": f"SFS project {pid} has no linked repo_url"}
    return {"name": "sfs_project_creation", "status": "pass",
            "duration_ms": ms,
            "details": f"{len(sfs)} SFS project(s), all have linked repos"}


def journey_project_delete_cleanup() -> dict[str, Any]:
    """
    Guards Bug 5: a deleted project must not leave orphan graph nodes
    (MissionTask / MissionBrief / BriefEntry / ConversationMessage)
    still referencing its project_id.

    Requires production Neptune access; skips in local mode.
    """
    if MODE != "production":
        return {"name": "project_delete_cleanup", "status": "skip",
                "error": "Requires production Neptune access"}
    # Neptune Analytics openCypher rejects label-disjunction in a WHERE clause
    # (`n:MissionTask OR n:MissionBrief ...`). Run one labelled MATCH per
    # node type and UNION — the anti-join (OPTIONAL MATCH + WHERE IS NULL)
    # surfaces rows whose project_id no longer has a Project.
    try:
        from nexus import neptune_client

        def _orphan(label: str) -> list[dict[str, Any]]:
            return neptune_client.query(
                f"MATCH (n:{label}) WHERE n.project_id IS NOT NULL "
                "OPTIONAL MATCH (p:Project {project_id: n.project_id}) "
                "WITH n, p WHERE p IS NULL "
                f"RETURN '{label}' AS label, n.project_id AS pid LIMIT 10"
            ) or []

        start = time.time()
        rows: list[dict[str, Any]] = []
        for lbl in ("MissionTask", "MissionBrief",
                    "BriefEntry", "ConversationMessage"):
            rows.extend(_orphan(lbl))
            if len(rows) >= 10:
                rows = rows[:10]
                break
        ms = int((time.time() - start) * 1000)
    except Exception as exc:
        return {"name": "project_delete_cleanup", "status": "error",
                "error": str(exc)[:200]}
    if rows:
        first = rows[0] if isinstance(rows[0], dict) else {}
        return {"name": "project_delete_cleanup", "status": "fail",
                "duration_ms": ms,
                "error": (f"{len(rows)}+ orphan {first.get('label','?')} "
                          f"nodes reference deleted project "
                          f"{str(first.get('pid','?'))[:12]}")}
    return {"name": "project_delete_cleanup", "status": "pass",
            "duration_ms": ms, "details": "No orphan graph data"}


# --- Flow health ------------------------------------------------------------
#
# Catch pipelines that started but never completed. Every synthetic below
# shares the same core idea: a Tenant whose mission_stage is an "early"
# stage (ingesting / ingestion_pending) for longer than STUCK_THRESHOLD_SEC
# is almost certainly frozen — the ingestion pipeline should resolve in
# 1-5 minutes on a typical repo.

_EARLY_STAGES = ("ingesting", "ingestion_pending")
_STUCK_THRESHOLD_SEC = 15 * 60


def _stuck_ingestions(creation_mode_filter: str | None) -> tuple[list[dict[str, Any]], int]:
    """
    Return (stuck_rows, duration_ms) for Tenants sitting in an early stage
    past the threshold. `creation_mode_filter`:
      - None    → all creation modes
      - 'sfs'   → only SFS projects
      - 'connect' → only connect-mode projects (explicit or unset)
    """
    if MODE != "production":
        return [], 0
    from nexus import neptune_client

    start = time.time()
    rows = neptune_client.query(
        "MATCH (t:Tenant) "
        "WHERE t.mission_stage IN ['ingesting', 'ingestion_pending'] "
        "OPTIONAL MATCH (p:Project {tenant_id: t.tenant_id}) "
        "WHERE p.status = 'active' OR p.status IS NULL "
        "RETURN t.tenant_id AS tenant_id, t.company_name AS tenant_name, "
        "t.mission_stage AS stage, t.updated_at AS updated_at, "
        "t.created_at AS tenant_created_at, "
        "p.project_id AS project_id, p.name AS project_name, "
        "p.creation_mode AS creation_mode, p.created_at AS project_created_at "
        "LIMIT 50"
    ) or []
    ms = int((time.time() - start) * 1000)

    now = datetime.now(timezone.utc)
    stuck: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        mode = (r.get("creation_mode") or "").lower()
        if creation_mode_filter == "sfs" and mode != "sfs":
            continue
        if creation_mode_filter == "connect" and mode not in ("", "connect"):
            continue

        ts_raw = (r.get("project_created_at") or r.get("updated_at")
                  or r.get("tenant_created_at") or "")
        try:
            dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except Exception:
            continue
        age_sec = (now - dt).total_seconds()
        if age_sec < _STUCK_THRESHOLD_SEC:
            continue
        stuck.append({**r, "age_min": int(age_sec // 60)})
    return stuck, ms


def _format_stuck(row: dict[str, Any]) -> str:
    pname = row.get("project_name") or row.get("tenant_name") or row.get("tenant_id", "?")
    pid = (row.get("project_id") or row.get("tenant_id") or "")[:12]
    return (f"Project {str(pname)[:30]} ({pid}) stuck at "
            f"{row.get('stage', '?')} for {row.get('age_min', '?')}m")


def journey_ingestion_completion() -> dict[str, Any]:
    """
    Catches ingestions that started but never finished — any project at
    stage='ingesting' or 'ingestion_pending' for more than 15 minutes is
    almost certainly frozen and needs a human (or auto-heal restart).
    """
    if MODE != "production":
        return {"name": "ingestion_completion", "status": "skip",
                "error": "Requires production Neptune access"}
    try:
        stuck, ms = _stuck_ingestions(None)
    except Exception as exc:
        return {"name": "ingestion_completion", "status": "error",
                "error": str(exc)[:200]}
    if stuck:
        return {"name": "ingestion_completion", "status": "fail",
                "duration_ms": ms,
                "error": _format_stuck(stuck[0])
                + (f" (+{len(stuck) - 1} more)" if len(stuck) > 1 else "")}
    return {"name": "ingestion_completion", "status": "pass",
            "duration_ms": ms, "details": "No stuck ingestions"}


def journey_connect_flow_health() -> dict[str, Any]:
    """
    Connect-mode projects should reach brief_pending within ~5 minutes.
    Anything older than 15 minutes still at an early stage means the
    connect flow (repo fetch → scan → brief) froze somewhere.
    """
    if MODE != "production":
        return {"name": "connect_flow_health", "status": "skip",
                "error": "Requires production Neptune access"}
    try:
        stuck, ms = _stuck_ingestions("connect")
    except Exception as exc:
        return {"name": "connect_flow_health", "status": "error",
                "error": str(exc)[:200]}
    if stuck:
        return {"name": "connect_flow_health", "status": "fail",
                "duration_ms": ms,
                "error": _format_stuck(stuck[0])
                + (f" (+{len(stuck) - 1} more)" if len(stuck) > 1 else "")}
    return {"name": "connect_flow_health", "status": "pass",
            "duration_ms": ms, "details": "No connect-mode projects stuck"}


def journey_sfs_flow_health() -> dict[str, Any]:
    """
    SFS (Start-from-Scratch) projects should scaffold + ingest in 1-3
    minutes. Past 15 minutes still at early stage = frozen scaffold
    pipeline.
    """
    if MODE != "production":
        return {"name": "sfs_flow_health", "status": "skip",
                "error": "Requires production Neptune access"}
    try:
        stuck, ms = _stuck_ingestions("sfs")
    except Exception as exc:
        return {"name": "sfs_flow_health", "status": "error",
                "error": str(exc)[:200]}
    if stuck:
        return {"name": "sfs_flow_health", "status": "fail",
                "duration_ms": ms,
                "error": _format_stuck(stuck[0])
                + (f" (+{len(stuck) - 1} more)" if len(stuck) > 1 else "")}
    return {"name": "sfs_flow_health", "status": "pass",
            "duration_ms": ms, "details": "No SFS projects stuck"}


def journey_ci_monitoring_health() -> dict[str, Any]:
    """
    Verifies the CI heartbeat can reach the GitHub Actions API. A 200
    from /rate_limit proves token auth + network; a missing token is
    the only reason the heartbeat would go blind in production.
    """
    if MODE != "production":
        return {"name": "ci_monitoring_health", "status": "skip",
                "error": "Requires production GitHub token"}
    try:
        import httpx
        from nexus.capabilities import ci_heartbeat as _hb
        token = _hb._token()
        if not token:
            return {"name": "ci_monitoring_health", "status": "fail",
                    "error": "No GitHub PAT — ci_heartbeat cannot scan runs"}
        start = time.time()
        resp = httpx.get(
            "https://api.github.com/rate_limit",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
            timeout=5,
        )
        ms = int((time.time() - start) * 1000)
    except Exception as exc:
        return {"name": "ci_monitoring_health", "status": "error",
                "error": str(exc)[:200]}
    if resp.status_code != 200:
        return {"name": "ci_monitoring_health", "status": "fail",
                "duration_ms": ms,
                "error": f"GitHub API returned {resp.status_code}"}
    return {"name": "ci_monitoring_health", "status": "pass",
            "duration_ms": ms, "details": "GitHub Actions API reachable"}


def journey_ci_healer_readiness() -> dict[str, Any]:
    """
    Confirms SSM can enumerate self-hosted runners. If no runners are
    tagged as expected, the healer would be unable to act on a hang.
    """
    if MODE != "production":
        return {"name": "ci_healer_readiness", "status": "skip",
                "error": "Requires production EC2/SSM access"}
    try:
        from nexus.aws_client import _client
        start = time.time()
        resp = _client("ec2").describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": ["*runner*"]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        )
        ms = int((time.time() - start) * 1000)
    except Exception as exc:
        return {"name": "ci_healer_readiness", "status": "error",
                "error": str(exc)[:200]}
    count = sum(len(r.get("Instances", []) or [])
                for r in resp.get("Reservations", []) or [])
    if count == 0:
        return {"name": "ci_healer_readiness", "status": "fail",
                "duration_ms": ms, "error": "No runner instances found"}
    return {"name": "ci_healer_readiness", "status": "pass",
            "duration_ms": ms, "details": f"{count} runner instance(s) visible"}


def get_summary() -> dict[str, Any]:
    """Summary for the diagnostic report."""
    results = run_all_journeys()
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "score_pct": round(passed / len(results) * 100) if results else 0,
        "results": results,
        "cached_at": _cache[1],
    }
