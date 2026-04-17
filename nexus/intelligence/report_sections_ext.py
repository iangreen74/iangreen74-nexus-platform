"""Learning Intelligence Report — sections 5 through 8."""
from __future__ import annotations

from collections import Counter
from typing import Any

from nexus.intelligence import report_queries as q


def section_5_learning_signal() -> str:
    runs = q.recent_dogfood_runs(hours=168)
    successes = [r for r in runs if r.get("status") == "success"]
    attempts = q.deploy_attempts(hours=168)
    attempts_by_pid = {a["project_id"]: a for a in attempts
                       if a.get("project_id")}

    lines = ["## 5. Learning Signal Attribution", ""]
    if not successes:
        lines.append("_No successful runs in 7 days. Learning signal is "
                      "zero until Section 1's success count rises._")
        return "\n".join(lines)

    lines.append(f"**{len(successes)} successes; "
                 f"{len(attempts_by_pid)} training records.**")
    lines.append("")
    missing = len(successes) - len(attempts_by_pid)
    if missing > 0:
        lines.append(f"⚠ **{missing} successes produced NO training record.**")
        lines.append("")
    lines.append("| Run | App | Quality | Training |")
    lines.append("|---|---|---|---|")
    for r in successes[:20]:
        a = attempts_by_pid.get(r.get("project_id"), {})
        qs = a.get("quality")
        lines.append(
            f"| `{(r.get('run_id') or '?')[:8]}` | {r.get('app', '?')[:15]} "
            f"| {f'{qs:.2f}' if isinstance(qs, (int, float)) else '—'} "
            f"| {'✓' if a else '✗'} |"
        )
    return "\n".join(lines)


def section_6_cost_economics() -> str:
    runs = q.recent_dogfood_runs(hours=24)
    successes = [r for r in runs if r.get("status") == "success"]
    attempts = q.deploy_attempts(hours=24)
    bedrock = q.bedrock_24h_cost()
    cost = bedrock.get("cost_usd", 0.0)
    mtd = bedrock.get("mtd_usd", 0.0)
    burn = bedrock.get("burn_rate_per_day", 0.0)

    lines = ["## 6. Cost Economics (last 24h)", ""]
    lines.append(f"**AWS spend today:** ${cost:.2f}")
    if mtd:
        lines.append(f"**Month-to-date:** ${mtd:.2f} "
                     f"(${burn:.2f}/day burn rate)")
    lines.append(f"**Dogfood runs:** {len(runs)}")
    lines.append(f"**Successes:** {len(successes)}")
    lines.append(f"**Training records:** {len(attempts)}")
    lines.append("")
    if len(runs) > 0:
        lines.append(f"- $/run: ${cost / len(runs):.2f}")
    if len(successes) > 0:
        lines.append(f"- $/success: ${cost / len(successes):.2f}")
    if len(attempts) > 0:
        lines.append(f"- $/training-record: ${cost / len(attempts):.2f}")
    elif runs:
        lines.append("- $/training-record: N/A (zero records)")
    if runs and not successes:
        lines.append(f"\n⚠ **$/success is infinite.** {len(runs)} runs, "
                     f"${cost:.2f}, zero training data.")
    return "\n".join(lines)


def section_7_trajectory() -> str:
    from nexus.intelligence.learning_snapshot import get_snapshots
    snapshots = get_snapshots(days=14)

    lines = ["## 7. Intelligence Trajectory", ""]
    if not snapshots:
        runs = q.recent_dogfood_runs(hours=168)
        successes = [r for r in runs if r.get("status") == "success"]
        lines.append("_No historical snapshots yet. First snapshot captures "
                      "after the daily scheduler fires._")
        lines.append("")
        daily = _by_day(successes)
        if daily:
            lines.append("**Successes by day (from run data):**")
            lines.append("")
            mx = max(daily.values()) if daily else 1
            for day in sorted(daily):
                c = daily[day]
                bar = "█" * int((c / mx) * 30) if mx else ""
                lines.append(f"  {day}  {bar} {c}")
        _, unique = q.pattern_fingerprint_counts()
        lines.append(f"\n**Fingerprints:** {len(successes)} successes "
                     f"→ {unique} unique fingerprints")
        return "\n".join(lines)

    lines.append(f"**{len(snapshots)} days of history:**")
    lines.append("")
    lines.append("### Successes per day")
    mx = max((int(s.get("successes") or 0) for s in snapshots), default=1) or 1
    for s in snapshots:
        date = s.get("date", "?")
        n = int(s.get("successes") or 0)
        bar = "█" * int((n / mx) * 30) if mx else ""
        lines.append(f"  {date}  {bar} {n}")
    lines.append("")
    lines.append("### Pattern library growth")
    mx2 = max((int(s.get("patterns") or 0) for s in snapshots), default=1) or 1
    for s in snapshots:
        date = s.get("date", "?")
        n = int(s.get("patterns") or 0)
        bar = "█" * int((n / mx2) * 30) if mx2 else ""
        lines.append(f"  {date}  {bar} {n}")
    return "\n".join(lines)


def _by_day(runs: list[dict[str, Any]]) -> dict[str, int]:
    d: dict[str, int] = {}
    for r in runs:
        c = r.get("created", "")
        if len(c) >= 10:
            d[c[:10]] = d.get(c[:10], 0) + 1
    return d


def section_8_anomalies() -> str:
    anomalies: list[str] = []

    try:
        from nexus import overwatch_graph
        batches = overwatch_graph.query(
            "MATCH (b:OverwatchDogfoodBatch) "
            "RETURN b.batch_id AS id, b.completed AS completed "
            "LIMIT 20"
        ) or []
        for b in batches:
            bid = b.get("id")
            if not bid:
                continue
            rows = overwatch_graph.query(
                "MATCH (r:OverwatchDogfoodRun {batch_id: $bid}) "
                "WHERE r.status IN ['success','failed','timeout'] "
                "RETURN count(r) AS c", {"bid": bid},
            )
            actual = int((rows[0].get("c") if rows else 0) or 0)
            reported = int(b.get("completed") or 0)
            if actual != reported:
                anomalies.append(
                    f"Batch `{str(bid)[:12]}`: counter={reported}, "
                    f"terminal runs={actual}")
    except Exception as e:
        anomalies.append(f"Batch audit failed: {e}")

    try:
        runs = q.recent_dogfood_runs(hours=168)
        successes = [r for r in runs if r.get("status") == "success"]
        attempts = q.deploy_attempts(hours=168)
        a_pids = {a.get("project_id") for a in attempts}
        orphans = [r for r in successes if r.get("project_id") not in a_pids]
        if orphans:
            anomalies.append(f"{len(orphans)} successful runs have no "
                             f"DeployAttempt record")
    except Exception as e:
        anomalies.append(f"Success→training audit failed: {e}")

    try:
        total, _ = q.pattern_fingerprint_counts()
        runs = q.recent_dogfood_runs(hours=168)
        successes = [r for r in runs if r.get("status") == "success"]
        if len(successes) > 3 and total == 0:
            anomalies.append(f"Pattern library empty despite "
                             f"{len(successes)} successes")
    except Exception as e:
        anomalies.append(f"Pattern library audit failed: {e}")

    lines = ["## 8. Anomalies & Self-Detected Inconsistencies", ""]
    if not anomalies:
        lines.append("_No inconsistencies detected. The system agrees with "
                      "itself on every observable invariant._")
    else:
        lines.append(f"**{len(anomalies)} inconsistencies:**")
        lines.append("")
        for a in anomalies:
            lines.append(f"- {a}")
    return "\n".join(lines)
