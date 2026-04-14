"""
CI Pattern Learning — antifragile memory of recurring CI hangs.

If the same (job, step) pair hangs 3+ times in 24h, it's a code/config
issue, not a transient glitch. This module aggregates CIIncident events
and, past threshold, upserts a FailurePattern (auto-incrementing on
MERGE) and writes a dashboard ActionRequired so the Deployment tile
reflects the recurring hang until it's fixed.

NOTE (Forgewing Cap 23 — future, not here): the same pattern store
will be reused for customer pipelines, letting ARIA tell a customer
"your `test` job hung on Docker pulls 4 times this week — here's a PR
that adds layer caching."
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_SAFE, MODE

logger = logging.getLogger("nexus.capabilities.ci_patterns")

_WINDOW_HOURS = 24
_MIN_OCCURRENCES_FOR_ANTIPATTERN = 3

# Seed hints for common hang causes so the generated brief reads like a
# diagnosis and not just a count. Keyed on substring matches against
# step name (lowercased).
_STEP_HINTS: list[tuple[str, str]] = [
    ("playwright", "`npx playwright install --with-deps` runs apt-get; "
                    "dpkg lock contention hangs concurrent jobs. "
                    "Drop --with-deps or bake deps into the runner image."),
    ("apt-get", "apt-get under concurrent jobs deadlocks on /var/lib/dpkg/lock. "
                "Move system-package install to the runner AMI."),
    ("docker pull", "Docker pull stalled — usually a rate-limited or slow registry. "
                    "Add layer caching or pin to a mirror."),
    ("docker build", "Docker build stalled — likely a large COPY or missing cache. "
                     "Enable BuildKit + cache-from."),
    ("npm install", "npm install stalled — lockfile or network issue. "
                    "Use `npm ci` and cache ~/.npm."),
    ("pip install", "pip install stalled — resolve or network issue. "
                    "Cache ~/.cache/pip and pin versions."),
]


def _events_in_window() -> list[dict[str, Any]]:
    """Pull CIIncident events from the last _WINDOW_HOURS."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=_WINDOW_HOURS)).isoformat()
    if MODE != "production":
        # Read directly from the local store so unit tests can exercise
        # the aggregation logic without going through Neptune.
        rows = [
            e for e in (overwatch_graph._local_store
                        .get("OverwatchPlatformEvent") or [])
            if e.get("event_type") == "ci_hung"
            and e.get("created_at", "") >= cutoff
        ]
        return rows
    return overwatch_graph.query(
        "MATCH (e:OverwatchPlatformEvent) "
        "WHERE e.event_type = 'ci_hung' AND e.created_at >= $cutoff "
        "RETURN e.id AS id, e.service AS service, e.details AS details, "
        "e.severity AS severity, e.created_at AS created_at "
        "ORDER BY e.created_at DESC LIMIT 500",
        {"cutoff": cutoff},
    ) or []


def _decode_details(ev: dict[str, Any]) -> dict[str, Any]:
    raw = ev.get("details")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _hint_for_step(step: str) -> str:
    s = (step or "").lower()
    for needle, msg in _STEP_HINTS:
        if needle in s:
            return msg
    return ""


def _build_brief(job: str, step: str, count: int,
                  repos: set[str], commits: list[str]) -> str:
    hint = _hint_for_step(step) or "No known remediation — surface to a human."
    recent = ", ".join(sorted(r for r in repos if r)[:3]) or "unknown repo"
    commit_tail = ", ".join(c for c in commits[:3] if c) or "unknown commits"
    return (
        f"{job}/{step or '<unknown step>'} hung {count} times in "
        f"{_WINDOW_HOURS}h on {recent} (recent commits: {commit_tail}). "
        f"{hint}"
    )


def learn_ci_patterns(**_: Any) -> dict[str, Any]:
    """
    Aggregate CIIncident events by (job_name, step). Any pair past the
    occurrence threshold becomes a FailurePattern and a dashboard action.
    """
    events = _events_in_window()
    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "repos": set(), "commits": [],
                 "last_seen": "", "runners": set()})

    for ev in events:
        d = _decode_details(ev)
        job = str(d.get("job_name") or "").strip()
        step = str(d.get("current_step") or "").strip()
        if not job:
            continue
        g = groups[(job, step)]
        g["count"] += 1
        if d.get("repo"):
            g["repos"].add(d["repo"])
        if d.get("commit"):
            g["commits"].append(d["commit"])
        if d.get("runner_name"):
            g["runners"].add(d["runner_name"])
        created = ev.get("created_at", "")
        if created > g["last_seen"]:
            g["last_seen"] = created

    antipatterns: list[dict[str, Any]] = []
    for (job, step), g in groups.items():
        if g["count"] < _MIN_OCCURRENCES_FOR_ANTIPATTERN:
            continue
        signature = f"ci_hang:{job}:{step}"
        brief = _build_brief(job, step, g["count"], g["repos"], g["commits"])
        try:
            overwatch_graph.record_failure_pattern(
                name=signature,
                signature=signature,
                diagnosis=brief,
                resolution=_hint_for_step(step) or "Human review required.",
                auto_healable=False,
                blast_radius=BLAST_SAFE,
                confidence=0.85,
            )
        except Exception:
            logger.exception("ci_patterns: record_failure_pattern failed")
        # Surface to the dashboard. Not tenant-specific — use a platform
        # pseudo-tenant key so the ActionBanner renderer treats it as a
        # global alert.
        try:
            overwatch_graph.write_tenant_action(
                tenant_id="__platform__",
                action_type=f"ci_antipattern:{job}:{step}"[:80],
                props={
                    "severity": "critical",
                    "title": f"Recurring CI hang: {job}/{step or '?'}",
                    "message": brief,
                    "category": "ci",
                    "count": g["count"],
                    "last_seen": g["last_seen"],
                },
            )
        except Exception:
            logger.exception("ci_patterns: write_tenant_action failed")
        antipatterns.append({
            "job": job, "step": step, "count": g["count"],
            "repos": sorted(g["repos"]), "runners": sorted(g["runners"]),
            "last_seen": g["last_seen"], "brief": brief,
        })

    return {
        "window_hours": _WINDOW_HOURS,
        "incidents_considered": len(events),
        "antipattern_count": len(antipatterns),
        "antipatterns": antipatterns,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


registry.register(Capability(
    name="learn_ci_patterns",
    function=learn_ci_patterns,
    blast_radius=BLAST_SAFE,
    description=(
        "Aggregate recent CIIncident events and upsert a FailurePattern + "
        "dashboard ActionRequired for any (job, step) past the 3-in-24h "
        "threshold."
    ),
))
