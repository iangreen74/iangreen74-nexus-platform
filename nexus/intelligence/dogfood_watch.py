"""Simple live watch for active dogfood batch.

Usage:
    python3 -m nexus.intelligence.dogfood_watch              # 2-min interval
    python3 -m nexus.intelligence.dogfood_watch --interval 30 # 30-sec interval
    python3 -m nexus.intelligence.dogfood_watch --once        # single snapshot
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from nexus import neptune_client, overwatch_graph


def snapshot() -> dict[str, Any] | None:
    """Summarize the active batch's state. None if no batch."""
    try:
        batch = overwatch_graph.get_active_batch()
    except Exception as e:
        return {"error": f"batch query failed: {e}"}
    if not batch:
        return None

    bid = batch.get("batch_id")
    try:
        runs = overwatch_graph.query(
            "MATCH (r:OverwatchDogfoodRun {batch_id: $bid}) "
            "RETURN r.id AS run_id, r.app_name AS app, "
            "r.status AS status, r.outcome AS outcome, "
            "r.project_id AS pid, r.duration_seconds AS dur "
            "ORDER BY r.created_at",
            {"bid": bid},
        ) or []
    except Exception as e:
        return {"error": f"run query failed: {e}", "batch_id": bid}

    stages = _fetch_stage_info([r.get("pid") for r in runs if r.get("pid")])

    completed = batch.get("completed") or 0
    successes = batch.get("successes") or 0
    rate = round(successes / completed, 2) if completed else 0.0

    return {
        "batch_id": bid,
        "completed": completed,
        "remaining": batch.get("remaining"),
        "success_rate": rate,
        "runs": runs,
        "stages": stages,
    }


def _fetch_stage_info(project_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Per-project brief/blueprint/task/PR counts from the Forgewing graph."""
    if not project_ids:
        return {}
    try:
        rows = neptune_client.query(
            "MATCH (p:Project) WHERE p.project_id IN $pids "
            "OPTIONAL MATCH (b:MissionBrief {project_id: p.project_id}) "
            "OPTIONAL MATCH (bp:ProductBlueprint {project_id: p.project_id}) "
            "OPTIONAL MATCH (t:MissionTask {project_id: p.project_id}) "
            "RETURN p.project_id AS pid, "
            "count(DISTINCT b) AS briefs, "
            "count(DISTINCT bp) AS blueprints, "
            "count(DISTINCT t) AS tasks, "
            "count(DISTINCT CASE WHEN t.pr_url IS NOT NULL THEN t END) AS prs",
            {"pids": project_ids},
        ) or []
        return {r.get("pid"): r for r in rows if isinstance(r, dict) and r.get("pid")}
    except Exception:
        return {}


def render(snap: dict[str, Any] | None) -> str:
    """Compact human-readable output."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if snap is None:
        return f"[{ts}] No active batch."
    if "error" in snap:
        return f"[{ts}] Error: {snap['error']}"

    runs = snap.get("runs", [])
    stages = snap.get("stages", {})
    by_status = Counter(r.get("status", "?") for r in runs)

    lines = [
        f"[{ts}] Batch {str(snap.get('batch_id',''))[:12]}: "
        f"completed={snap.get('completed')} remaining={snap.get('remaining')} "
        f"success_rate={snap.get('success_rate')}",
        f"       Status: {dict(by_status)}",
    ]
    for r in runs:
        pid = r.get("pid") or ""
        st = stages.get(pid, {})
        briefs = st.get("briefs", 0)
        bps = st.get("blueprints", 0)
        tasks = st.get("tasks", 0)
        prs = st.get("prs", 0)
        marker = "🎯" if bps and int(bps) > 0 else ("🟡" if briefs and int(briefs) > 0 else "  ")
        app = (r.get("app") or "?")[:16]
        status = str(r.get("status", "?"))
        outcome = str(r.get("outcome") or "")[:22]
        lines.append(
            f"  {marker} {app:16} b={briefs} bp={bps} t={tasks} pr={prs} "
            f"| {status:10} | {outcome}"
        )
    return "\n".join(lines)


def run(interval: int = 120, once: bool = False) -> int:
    """Main loop. Prints snapshots until Ctrl-C."""
    print(f"# Dogfood live watch — interval {interval}s  (Ctrl-C to stop)\n")
    try:
        while True:
            snap = snapshot()
            print(render(snap))
            print()
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n# Watch stopped.")
        return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Live watch for the active dogfood batch.")
    parser.add_argument("--interval", type=int, default=120,
                        help="Seconds between snapshots (default 120)")
    parser.add_argument("--once", action="store_true",
                        help="Print a single snapshot and exit")
    args = parser.parse_args()
    return run(interval=args.interval, once=args.once)


if __name__ == "__main__":
    sys.exit(_cli())
