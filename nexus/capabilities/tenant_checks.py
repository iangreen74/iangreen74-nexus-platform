"""Tenant-level Phase 1 aggregator.

Combines tenant_health, tenant_validator, and capability_validator so a
tenant diagnosis starts with the full per-tenant sensor picture. Every
finding is a human-readable string for Bedrock synthesis.

All sync — callers hop into a thread via asyncio.to_thread().
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _safe(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.debug("tenant check %s failed: %s", getattr(fn, "__name__", fn), exc)
        return None


def _health_findings(tid: str) -> list[str]:
    from nexus.sensors import tenant_health
    snap = _safe(tenant_health.check_tenant, tid) or {}
    if not snap or snap.get("error"):
        return [f"tenant_health unavailable for {tid[:12]}"]

    out: list[str] = []
    ctx = snap.get("context") or {}
    stage = ctx.get("mission_stage", "unknown")
    status = snap.get("overall_status", "unknown")
    out.append(f"Tenant {tid[:12]}: stage={stage}, status={status}")

    dep = snap.get("deployment") or {}
    out.append(f"Deployment: provisioned={dep.get('provisioned')}, "
               f"healthy={dep.get('healthy')}, reachable={dep.get('reachable')}"
               + (f", reason={dep.get('reason')}" if dep.get("reason") else ""))

    if snap.get("deploy_stuck"):
        out.append(f"Deploy STUCK at stage={snap.get('deploy_stage', '?')}")

    pipe = snap.get("pipeline") or {}
    pipe_bits = [
        f"{pipe.get('pr_count', 0)} PRs",
        f"{pipe.get('tasks_complete', 0)} complete",
        f"{pipe.get('tasks_pending', 0)} pending",
    ]
    stuck = pipe.get("tasks_stuck")
    if stuck:
        pipe_bits.append(f"{stuck} stuck")
    out.append("Pipeline: " + ", ".join(pipe_bits) +
               f" (stage={snap.get('pipeline_stage', '?')})")

    token = snap.get("token") or {}
    out.append(f"Token: present={token.get('present', False)}, "
               f"installation_id={token.get('installation_id', 'missing')}")

    conv = snap.get("conversation") or {}
    msg_count = conv.get("message_count", 0)
    last = conv.get("last_activity_ago_hours")
    if msg_count or last is not None:
        out.append(f"Conversation: {msg_count} messages" +
                   (f", last activity {last:.1f}h ago" if last is not None else ""))

    feats = snap.get("deploy_features") or {}
    if feats:
        out.append(f"Deploy features: preview={feats.get('preview_available')}, "
                   f"smoke={feats.get('smoke_test_available')}" +
                   (f", pass_rate={feats.get('smoke_pass_rate')}"
                    if feats.get("smoke_pass_rate") is not None else ""))
    return out


def _validator_findings(tid: str) -> list[str]:
    from nexus.sensors import tenant_validator
    alerts = _safe(tenant_validator.validate_tenant, tid) or []
    if not alerts:
        return []
    out = [f"Validation: {len(alerts)} alert(s)"]
    for a in alerts[:8]:
        out.append(f"  - [{a.get('severity', '?')}] {a.get('check', '?')}: "
                   f"{str(a.get('message', ''))[:160]}")
    return out


def _capability_findings(tid: str) -> list[str]:
    from nexus.sensors import capability_validator
    report = _safe(capability_validator.validate_tenant_capabilities, tid)
    if not report:
        return []
    score = capability_validator.capability_score(report)
    out = [f"Capabilities: {score}, overall={report.overall}"]
    warned = [c for c in report.checks if c.status == "warn"]
    failed = [c for c in report.checks if c.status == "fail"]
    if failed:
        out.append(f"  FAILING ({len(failed)}): "
                   + ", ".join(f"{c.layer}/{c.check}" for c in failed[:6]))
        for c in failed[:4]:
            out.append(f"    - {c.layer}/{c.check}: {str(c.detail)[:160]}")
    if warned:
        out.append(f"  WARNINGS ({len(warned)}): "
                   + ", ".join(f"{c.layer}/{c.check}" for c in warned[:6]))
    return out


def tenant_quick_checks(tenant_id: str) -> list[str]:
    """Aggregate every per-tenant sensor into Phase 1 findings. Never raises."""
    tenant_id = (tenant_id or "").strip()
    if not tenant_id:
        return ["tenant_id is required"]

    from nexus import neptune_client
    rows = _safe(neptune_client.query,
                 "MATCH (t:Tenant {tenant_id: $tid}) RETURN t.tenant_id AS tid",
                 {"tid": tenant_id})
    if not rows:
        return [f"Tenant {tenant_id[:12]} not found in graph"]

    out: list[str] = []
    for fn in (_health_findings, _validator_findings, _capability_findings):
        try:
            out.extend(fn(tenant_id))
        except Exception as exc:
            logger.debug("tenant aggregator %s failed: %s", fn.__name__, exc)
            out.append(f"{fn.__name__}: {type(exc).__name__}: {str(exc)[:120]}")
    return out
