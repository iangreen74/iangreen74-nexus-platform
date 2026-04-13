"""Goal-level Phase 1 aggregator.

Pulls from every platform-wide sensor so the Goal diagnosis has real
content to analyze. Each finding is a human-readable string; the caller
concatenates them into Phase 1 output and feeds them to Phase 2's
Bedrock synthesizer.

All sensors are wrapped in try/except — one broken signal must not
silence the rest. Sync functions here because callers hop into a thread
via asyncio.to_thread().
"""
from __future__ import annotations

import concurrent.futures
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SENSOR_TIMEOUT_SEC = 10.0


def _safe(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.debug("goal check %s failed: %s", getattr(fn, "__name__", fn), exc)
        return None


def _with_timeout(fn, label: str, timeout: float = _SENSOR_TIMEOUT_SEC) -> list[str]:
    """Run fn() with a hard timeout. Returns findings list (or timeout note)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout) or []
        except concurrent.futures.TimeoutError:
            return [f"{label}: timed out after {timeout:.0f}s"]
        except Exception as exc:
            return [f"{label}: {type(exc).__name__}: {str(exc)[:120]}"]


def _feature_health_findings() -> list[str]:
    """Any non-healthy feature tiles get listed by status_line."""
    import asyncio
    out: list[str] = []
    try:
        from nexus.capabilities.feature_health import get_all_feature_health
        # get_all_feature_health is async; run it in a fresh loop since
        # goal_quick_checks itself runs in a worker thread.
        data = asyncio.run(get_all_feature_health()) or {}
    except Exception as exc:
        return [f"feature_health unavailable: {type(exc).__name__}: {str(exc)[:100]}"]
    overall = data.get("overall", "unknown")
    features = data.get("features", {}) or {}
    bad = [f for f in features.values() if f.get("status") not in ("healthy", None)]
    if bad:
        out.append(f"Feature rollup: overall={overall}, {len(bad)}/{len(features)} non-healthy")
        for f in bad[:6]:
            out.append(f"  - {f.get('name', '?')} [{f.get('status', '?')}]: "
                       f"{f.get('status_line', '')}")
    return out


def _tenant_health_findings() -> list[str]:
    out: list[str] = []
    from nexus.sensors import tenant_health
    reports = _safe(tenant_health.check_all_tenants) or []
    bad = [r for r in reports
           if r.get("overall_status") not in ("healthy", None)
           or r.get("deploy_stuck")]
    if bad:
        out.append(f"Tenant rollup: {len(bad)}/{len(reports)} tenants degraded/critical")
        for r in bad[:5]:
            ctx = r.get("context") or {}
            out.append(f"  - {str(r.get('tenant_id', ''))[:12]} "
                       f"stage={ctx.get('mission_stage', '?')} "
                       f"status={r.get('overall_status', '?')} "
                       f"deploy_stuck={r.get('deploy_stuck', False)}")
    return out


def _ci_findings() -> list[str]:
    from nexus.sensors import ci_monitor
    data = _safe(ci_monitor.check_ci) or {}
    out: list[str] = []
    rate = data.get("green_rate_24h")
    if rate is not None and rate < 0.95:
        out.append(f"CI green rate {rate*100:.0f}% (below 95%) across "
                   f"{data.get('run_count', 0)} runs")
    failing = data.get("failing_workflows") or []
    if failing:
        out.append(f"CI failing workflows: {', '.join(str(w) for w in failing[:6])}")
    if data.get("last_run_status") not in ("success", None, "unknown"):
        out.append(f"CI last run: {data.get('last_run_status')}")
    return out


def _daemon_findings() -> list[str]:
    from nexus.sensors import daemon_monitor
    data = _safe(daemon_monitor.check_daemon) or {}
    out: list[str] = []
    if not data.get("running"):
        out.append(f"Daemon not running (desired={data.get('desired_count', '?')}, "
                   f"running={data.get('running_count', '?')})")
    if data.get("stale"):
        age = data.get("cycle_age_minutes")
        out.append(f"Daemon cycle stale — last cycle {age}m ago" if age is not None
                   else "Daemon cycle stale")
    if (data.get("error_count_30m") or 0) > 0:
        out.append(f"Daemon errors last 30m: {data.get('error_count_30m')}")
    return out


def _infra_lock_findings() -> list[str]:
    from nexus.sensors import infrastructure_lock
    data = _safe(infrastructure_lock.check_locks) or {}
    violations = data.get("violations") or []
    if violations:
        out = [f"Infrastructure locks: {len(violations)} violation(s)"]
        for v in violations[:5]:
            out.append(f"  - {v.get('lock', '?')}: {str(v.get('reason', ''))[:150]}")
        return out
    return []


def _runner_findings() -> list[str]:
    from nexus import runner_health
    data = _safe(runner_health.get_summary) or {}
    total = data.get("total", 0)
    healthy = data.get("healthy", 0)
    if total and healthy < total:
        return [f"Runners: {healthy}/{total} healthy, {data.get('errors', 0)} errored"]
    return []


def _heal_chain_findings() -> list[str]:
    try:
        from nexus.reasoning.executor import get_all_active_chains
        chains = get_all_active_chains() or {}
    except Exception:
        return []
    if not chains:
        return []
    out = [f"Active heal chains: {len(chains)}"]
    for key, ch in list(chains.items())[:5]:
        if isinstance(ch, dict):
            out.append(f"  - {ch.get('chain', '?')} step={ch.get('step', '?')} "
                       f"source={ch.get('source', key)}")
    return out


def _validator_findings() -> list[str]:
    from nexus.sensors import tenant_validator
    by_tenant = _safe(tenant_validator.validate_all_tenants) or {}
    alerts = [(tid, a) for tid, alist in by_tenant.items() for a in (alist or [])]
    if not alerts:
        return []
    out = [f"Tenant validation: {len(alerts)} alert(s) across "
           f"{sum(1 for _, alist in by_tenant.items() if alist)} tenants"]
    for tid, a in alerts[:6]:
        out.append(f"  - {str(tid)[:12]} [{a.get('severity', '?')}] "
                   f"{a.get('check', '?')}: {str(a.get('message', ''))[:150]}")
    return out


def _synthetic_findings() -> list[str]:
    from nexus.synthetic_tests import get_summary
    data = _safe(get_summary) or {}
    if not data:
        return []
    failed = [r for r in (data.get("results") or []) if r.get("status") in ("fail", "error")]
    if failed:
        out = [f"Synthetics: {data.get('passed', 0)}/{data.get('total', 0)} passing, "
               f"{len(failed)} failing"]
        for r in failed[:5]:
            out.append(f"  - {r.get('name', '?')}: "
                       f"{str(r.get('error', r.get('status', '')))[:150]}")
        return out
    return []


def goal_quick_checks() -> list[str]:
    """Aggregate every platform signal into Phase 1 findings. Never raises."""
    out: list[str] = []
    for fn in (_feature_health_findings, _tenant_health_findings, _ci_findings,
               _daemon_findings, _infra_lock_findings, _runner_findings,
               _heal_chain_findings, _validator_findings, _synthetic_findings):
        out.extend(_with_timeout(fn, fn.__name__))
    return out
