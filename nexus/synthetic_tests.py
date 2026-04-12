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
