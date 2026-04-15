"""
Sprint Context — release-readiness items injected into every Goal report.

Each item either carries a live check (function queried on demand) or is
a manual gate the operator flips with `set_item_status`. Manual
overrides persist and take precedence over live checks so an item can
be pinned done/blocked even if its automated probe would say otherwise.

Goal diagnoses read `format_for_report` / `summarize_one_line` so every
report carries release context. In local mode live checks return None
(unknown) and we fall back to the baseline status.
"""
from __future__ import annotations

import logging
import threading
from copy import deepcopy
from typing import Any, Callable

from nexus import neptune_client
from nexus.config import MODE

logger = logging.getLogger(__name__)

VALID_STATUSES = ("done", "in_progress", "not_started", "blocked")

# --- Live checks ------------------------------------------------------------


def _count(cypher: str) -> int | None:
    """Return a single count scalar, or None when Neptune is unreachable."""
    if MODE != "production":
        return None
    rows = neptune_client.query(cypher) or []
    if not rows or not isinstance(rows[0], dict):
        return 0
    for v in rows[0].values():
        try:
            return int(v)
        except Exception:
            continue
    return 0


def _check_pr_generation() -> bool | None:
    c = _count("MATCH (t:MissionTask) WHERE t.status='complete' AND "
               "t.pr_url IS NOT NULL RETURN count(t) AS c")
    return None if c is None else c > 0


def _check_brief_isolation() -> bool | None:
    try:
        from nexus.synthetic_tests import journey_project_isolation_audit
        r = journey_project_isolation_audit()
    except Exception:
        return None
    return r.get("status") == "pass" if r.get("status") != "skip" else None


def _check_repofile_isolation() -> bool | None:
    try:
        from nexus.synthetic_tests import journey_merge_key_audit
        r = journey_merge_key_audit()
    except Exception:
        return None
    return r.get("status") == "pass" if r.get("status") != "skip" else None


def _check_ci_runners() -> bool | None:
    if MODE != "production":
        return None
    try:
        from nexus.aws_client import _client
        resp = _client("ec2").describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": ["*runner*"]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        )
        count = sum(len(r.get("Instances", []) or [])
                    for r in resp.get("Reservations", []) or [])
        return count >= 4
    except Exception:
        return None


def _check_sfs_flow() -> bool | None:
    c = _count("MATCH (p:Project) WHERE (p.creation_mode='sfs' OR p.forge_sfs=true) "
               "AND p.tenant_id IS NOT NULL "
               "MATCH (t:MissionTask {tenant_id: p.tenant_id}) "
               "WHERE t.pr_url IS NOT NULL RETURN count(DISTINCT p) AS c")
    return None if c is None else c > 0


def _check_test_screen() -> bool | None:
    c = _count("MATCH (d:DeploymentProgress) WHERE d.stage='live' RETURN count(d) AS c")
    return None if c is None else c > 0


# --- Item definitions -------------------------------------------------------


DEFAULT_SPRINT_ITEMS: list[dict[str, Any]] = [
    {"id": "pr_generation", "name": "PR generation working",
     "status": "done", "live_check": _check_pr_generation},
    {"id": "brief_isolation", "name": "Brief isolation (7 leaks fixed)",
     "status": "done", "live_check": _check_brief_isolation},
    {"id": "repofile_isolation", "name": "RepoFile project scoping",
     "status": "done", "live_check": _check_repofile_isolation},
    {"id": "ci_runners", "name": "4 runners operational",
     "status": "done", "live_check": _check_ci_runners},
    {"id": "aria_ux", "name": "ARIA discovery flow + dot",
     "status": "done", "live_check": None},
    {"id": "sfs_flow", "name": "SFS end-to-end verified",
     "status": "in_progress", "live_check": _check_sfs_flow},
    {"id": "incognito_walkthrough", "name": "Incognito walkthrough",
     "status": "not_started", "live_check": None},
    {"id": "test_screen", "name": "Test screen features",
     "status": "not_started", "live_check": _check_test_screen},
    {"id": "waitlist_email", "name": "Waitlist email to 20 companies",
     "status": "not_started", "live_check": None},
]

_lock = threading.Lock()
_items: list[dict[str, Any]] = deepcopy(DEFAULT_SPRINT_ITEMS)
_manual_overrides: set[str] = set()


def reset() -> None:
    global _items, _manual_overrides
    with _lock:
        _items = deepcopy(DEFAULT_SPRINT_ITEMS)
        _manual_overrides = set()


def _evaluate(item: dict[str, Any]) -> tuple[str, str]:
    """Return (effective_status, source). Source ∈ live/manual/baseline."""
    if item["id"] in _manual_overrides:
        return item["status"], "manual"
    check: Callable[[], bool | None] | None = item.get("live_check")
    if check is not None:
        try:
            result = check()
        except Exception:
            logger.exception("live_check failed for %s", item["id"])
            result = None
        if result is True:
            return "done", "live"
        if result is False:
            return "blocked", "live"
    return item["status"], "baseline"


def get_items() -> list[dict[str, Any]]:
    """Snapshot of items with effective live status applied."""
    with _lock:
        out: list[dict[str, Any]] = []
        for raw in _items:
            effective, source = _evaluate(raw)
            out.append({
                "id": raw["id"],
                "name": raw["name"],
                "status": effective,
                "source": source,
                "manual_override": raw["id"] in _manual_overrides,
            })
        return out


def set_item_status(item_id: str, status: str) -> bool:
    """Manually pin an item's status. Persists until `reset()`."""
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}")
    with _lock:
        for item in _items:
            if item["id"] == item_id:
                item["status"] = status
                _manual_overrides.add(item_id)
                return True
        return False


def clear_override(item_id: str) -> bool:
    with _lock:
        if item_id in _manual_overrides:
            _manual_overrides.discard(item_id)
            return True
        return False


def get_status() -> dict[str, Any]:
    items = get_items()
    done = [i for i in items if i["status"] == "done"]
    in_progress = [i for i in items if i["status"] == "in_progress"]
    not_started = [i for i in items if i["status"] == "not_started"]
    blocked = [i for i in items if i["status"] == "blocked"]
    return {
        "total": len(items),
        "done": len(done),
        "in_progress": len(in_progress),
        "not_started": len(not_started),
        "blocked": len(blocked),
        "complete_pct": round(len(done) / len(items) * 100) if items else 0,
        "blocker_names": [i["name"] for i in (in_progress + not_started + blocked)],
        "items": items,
    }


def format_for_report() -> str:
    status = get_status()
    lines = [
        "## Release Readiness",
        f"**{status['done']}/{status['total']} items complete** "
        f"({status['complete_pct']}%)",
        "",
    ]
    for item in status["items"]:
        marker = {"done": "✅", "in_progress": "🔄",
                  "not_started": "⏳", "blocked": "🚫"}.get(item["status"], "·")
        src = item.get("source", "baseline")
        tag = f" _{src}_" if src != "baseline" else ""
        lines.append(f"- {marker} **{item['name']}** — {item['status']}{tag}")
    blockers = status["blocker_names"]
    if blockers:
        lines += ["", f"**Blockers:** {', '.join(blockers)}"]
    return "\n".join(lines)


def summarize_one_line() -> str:
    s = get_status()
    base = f"Release readiness: {s['done']}/{s['total']} complete"
    blockers = s["blocker_names"]
    if not blockers:
        return base + " — all items done."
    return (base + ". Blockers: " + ", ".join(blockers[:5])
            + (f" (+{len(blockers)-5} more)" if len(blockers) > 5 else "") + ".")
