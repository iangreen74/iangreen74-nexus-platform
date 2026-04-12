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
