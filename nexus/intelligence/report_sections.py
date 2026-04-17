"""Learning Intelligence Report — sections 1 through 4."""
from __future__ import annotations

from collections import Counter

from nexus.intelligence import capability_matrix as cm
from nexus.intelligence import report_queries as q


def section_1_executive_summary() -> str:
    runs = q.recent_dogfood_runs(hours=168)
    completed = [r for r in runs if r.get("status") in ("success", "failed", "timeout")]
    successes = [r for r in completed if r.get("status") == "success"]

    attempts = q.deploy_attempts(hours=168)
    scores = [a["quality"] for a in attempts
              if isinstance(a.get("quality"), (int, float))]
    avg_q = (sum(scores) / len(scores)) if scores else None

    daily = max(1, len(successes) / 7.0)
    remaining = max(0, 1000 - len(successes))
    days = int(remaining / daily) if daily > 0 else 9999

    lines = ["## 1. Executive Summary", ""]
    lines.append(f"**Runs this week:** {len(runs)} "
                 f"({len(completed)} completed, {len(successes)} successes)")
    lines.append(f"**Avg code quality:** "
                 f"{f'{avg_q:.2f}' if avg_q else 'no data'}")
    lines.append(f"**Days to fine-tune threshold (1000):** "
                 f"{days if days < 9999 else '∞ (velocity = 0)'}")
    lines.append("")
    lines.append(_synthesis(runs, completed, successes))
    return "\n".join(lines)


def _synthesis(runs, completed, successes) -> str:
    if not runs:
        return ("_No dogfood activity in the last 7 days. "
                "The flywheel is not turning._")
    rate = len(successes) / len(completed) if completed else 0
    if rate == 0 and runs:
        return (f"_{len(runs)} runs kicked off with zero successes. "
                f"Pipeline exercised but producing no training data. "
                f"Check Section 4 for failure-mode taxonomy._")
    if rate < 0.2:
        return f"_Success rate {rate:.0%} is below healthy. See Section 4._"
    return (f"_{len(successes)} successes from {len(completed)} completed "
            f"({rate:.0%}). Flywheel is turning. See Section 3._")


def section_2_causal_chain() -> str:
    runs = q.recent_dogfood_runs(hours=48)
    if not runs:
        return "## 2. Per-Run Causal Chain\n\n_No runs in the last 48 hours._"

    pids = [r["project_id"] for r in runs if r.get("project_id")]
    tasks_map: dict[str, list] = {}
    for t in q.mission_tasks_for_runs(pids):
        tasks_map.setdefault(t.get("project_id"), []).append(t)

    lines = ["## 2. Per-Run Causal Chain (last 48h)", ""]
    lines.append("| Run | App | Kickoff | Deploy | Outcome |")
    lines.append("|---|---|---|---|---|")
    for r in runs[:30]:
        tasks = tasks_map.get(r.get("project_id"), [])
        lines.append(_causal_row(r, tasks))
    if len(runs) > 30:
        lines.append(f"\n_... {len(runs) - 30} more_")
    return "\n".join(lines)


def _causal_row(run, tasks) -> str:
    status = run.get("status", "?")
    outcome = run.get("outcome") or status
    app = (run.get("app") or "?")[:15]
    rid = (run.get("run_id") or "?")[:8]
    kick = "✓" if status != "kick_failed" else "✗"
    if outcome == "deploy_never_started":
        deploy = "✗ never started"
    elif outcome == "success":
        deploy = "✓"
    elif outcome in ("failed", "timeout"):
        deploy = "✗"
    else:
        deploy = "?"
    return f"| `{rid}` | {app} | {kick} | {deploy} | `{outcome}` |"


def section_3_pattern_library() -> str:
    total, unique = q.pattern_fingerprint_counts()
    counts = cm.status_counts()

    lines = ["## 3. Pattern Library Health", ""]
    lines.append(f"**Fingerprints captured:** {total} total, {unique} unique")
    lines.append("")
    if total == 0:
        lines.append("_No patterns captured yet. Either no deploys have "
                      "reached pattern-extraction, or the writer isn't firing._")
        lines.append("")
    lines.append("### Capability Matrix")
    lines.append(f"proven={counts.get('proven', 0)}, "
                 f"architected={counts.get('architected', 0)}, "
                 f"roadmap={counts.get('roadmap', 0)}")
    lines.append("")
    lines.append(cm.render_matrix())
    return "\n".join(lines)


def section_4_failure_taxonomy() -> str:
    runs = q.recent_dogfood_runs(hours=168)
    failures = [r for r in runs if r.get("status") in ("failed", "timeout")]

    lines = ["## 4. Failure Mode Taxonomy", ""]
    if not failures:
        lines.append("_No failures in 7 days. Either healthy, or nothing "
                      "is being attempted — check Section 1._")
        return "\n".join(lines)

    by_outcome = Counter(r.get("outcome") or r.get("status") or "?"
                         for r in failures)
    lines.append(f"**{len(failures)} failures, "
                 f"{len(by_outcome)} distinct outcomes:**")
    lines.append("")
    for outcome, count in by_outcome.most_common():
        affected = sorted({r.get("tenant_id") for r in failures
                          if (r.get("outcome") or r.get("status")) == outcome
                          and r.get("tenant_id")})
        samples = [r.get("reason") for r in failures[:3]
                   if r.get("reason")
                   and (r.get("outcome") or r.get("status")) == outcome]
        lines.append(f"### `{outcome}` — {count} occurrences")
        lines.append(f"- Tenants: "
                     f"{', '.join(t[:18] for t in affected) or '—'}")
        if samples:
            lines.append(f"- Sample: _{samples[0][:140]}_")
        lines.append("")
    return "\n".join(lines)
