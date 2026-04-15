"""
Sprint Context — release-readiness items injected into every Goal report.

Hardcoded list of the current sprint's shippable items and their state.
Goal diagnoses read this so every report carries release context:

  "Release readiness: 6/9 items complete. Blockers: incognito walkthrough,
   test screen, waitlist email."

The list is intentionally code-literal for now — a POST endpoint to
flip statuses is a follow-up. Overriding in-process lets tests exercise
the status transitions without touching the production list.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any

VALID_STATUSES = ("done", "in_progress", "not_started", "blocked")

# Current release cycle as of 2026-04-14. Keep in source for now; a
# small admin route can flip these once we pin down a UI for it.
DEFAULT_SPRINT_ITEMS: list[dict[str, str]] = [
    {"id": "pr_generation",         "name": "PR generation working",
     "status": "done"},
    {"id": "brief_isolation",       "name": "Brief isolation (7 leaks fixed)",
     "status": "done"},
    {"id": "repofile_isolation",    "name": "RepoFile project scoping",
     "status": "done"},
    {"id": "ci_runners",            "name": "4 runners operational",
     "status": "done"},
    {"id": "aria_ux",               "name": "ARIA discovery flow + dot",
     "status": "done"},
    {"id": "sfs_flow",              "name": "SFS end-to-end verified",
     "status": "in_progress"},
    {"id": "incognito_walkthrough", "name": "Incognito walkthrough",
     "status": "not_started"},
    {"id": "test_screen",           "name": "Test screen features",
     "status": "not_started"},
    {"id": "waitlist_email",        "name": "Waitlist email to 20 companies",
     "status": "not_started"},
]

_lock = threading.Lock()
_items: list[dict[str, str]] = deepcopy(DEFAULT_SPRINT_ITEMS)


def reset() -> None:
    """Test hook — restore the default list."""
    global _items
    with _lock:
        _items = deepcopy(DEFAULT_SPRINT_ITEMS)


def get_items() -> list[dict[str, str]]:
    """Snapshot of the current items list."""
    with _lock:
        return deepcopy(_items)


def set_item_status(item_id: str, status: str) -> bool:
    """Update one item's status. Returns True if the id existed."""
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}")
    with _lock:
        for item in _items:
            if item["id"] == item_id:
                item["status"] = status
                return True
        return False


def get_status() -> dict[str, Any]:
    """
    Aggregate counts + named blocker list for the sprint. 'blockers' is
    the subset of items that are NOT done (in_progress, not_started,
    blocked) — the things standing between now and ship.
    """
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
    """Markdown block for the Goal diagnosis."""
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
        lines.append(f"- {marker} **{item['name']}** — {item['status']}")
    blockers = status["blocker_names"]
    if blockers:
        lines += ["", f"**Blockers:** {', '.join(blockers)}"]
    return "\n".join(lines)


def summarize_one_line() -> str:
    """Single sentence safe for the synthesis evidence block."""
    s = get_status()
    base = f"Release readiness: {s['done']}/{s['total']} complete"
    blockers = s["blocker_names"]
    if not blockers:
        return base + " — all items done."
    return (base + ". Blockers: " + ", ".join(blockers[:5])
            + (f" (+{len(blockers)-5} more)" if len(blockers) > 5 else "") + ".")
