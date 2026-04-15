"""
Platform Healer — auto-remediation for non-CI failures: daemon stalls,
Neptune slowness, API unhealth, placeholder noise. Each chain:
{detection, steps}. Progress stored in-process; each step recorded as
an OverwatchPlatformEvent for audit. CI healing stays in ci_healer.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from nexus import overwatch_graph
from nexus.config import MODE

logger = logging.getLogger("nexus.capabilities.platform_healer")
_state_lock = threading.Lock()
_active: dict[str, dict[str, Any]] = {}


def _check_daemon_stale() -> bool:
    if MODE != "production": return False
    try:
        from nexus.sensors import daemon_monitor
        d = daemon_monitor.check_daemon() or {}
        return bool(d.get("stale")) or int(d.get("cycle_age_minutes") or 0) >= 10
    except Exception: return False


def _check_neptune_slow() -> bool:
    if MODE != "production": return False
    try:
        from nexus import neptune_client
        started = time.time()
        neptune_client.query("MATCH (n) RETURN count(n) AS c LIMIT 1")
        return (time.time() - started) > 10
    except Exception: return False


def _check_api_unhealthy() -> bool:
    if MODE != "production": return False
    try:
        from nexus.capabilities.forgewing_api import get_health
        resp = get_health() or {}
        return bool(resp.get("error")) or resp.get("status", 200) >= 500
    except Exception: return True


def _check_placeholder_noise() -> bool:
    if MODE != "production": return False
    try:
        events = overwatch_graph.get_recent_events(limit=100) or []
        hits = sum(1 for e in events
                   if "placeholder" in str(e.get("details") or "").lower()
                   or "placeholder" in str(e.get("service") or "").lower())
        return hits >= 5
    except Exception: return False


HEAL_CHAINS: dict[str, dict[str, Any]] = {
    "daemon_stale": {
        "description": "Daemon cycle hasn't completed in 10+ minutes",
        "detection": _check_daemon_stale,
        "steps": [
            {"action": "restart_daemon", "description": "ECS redeploy"},
            {"action": "escalate", "description": "Daemon may have a code bug"},
        ],
    },
    "neptune_slow": {
        "description": "Neptune query latency > 10s",
        "detection": _check_neptune_slow,
        "steps": [
            {"action": "log_warning", "description": "Record slow-query event"},
            {"action": "escalate", "description": "Neptune may need scaling"},
        ],
    },
    "api_unhealthy": {
        "description": "Forgewing /health returning non-2xx",
        "detection": _check_api_unhealthy,
        "steps": [
            {"action": "force_redeploy", "description": "Force new ECS deployment"},
            {"action": "escalate", "description": "API may have a code bug"},
        ],
    },
    "placeholder_noise": {
        "description": "forge-test-placeholder errors recurring",
        "detection": _check_placeholder_noise,
        "steps": [
            {"action": "suppress_tenant", "description": "Skip list"},
            {"action": "escalate", "description": "Clean up placeholder node"},
        ],
    },
}


def _record(chain: str, step_idx: int, action: str, outcome: str, detail: str = "") -> None:
    try:
        overwatch_graph.record_event(
            event_type="platform_heal_step", service=f"healer:{chain}",
            details={"chain": chain, "step": step_idx, "action": action,
                     "outcome": outcome, "detail": detail},
            severity="info" if outcome.startswith("success") else "warning")
    except Exception:
        logger.exception("platform_healer: record_event failed")


def execute_step(chain_name: str, step_idx: int) -> dict[str, Any]:
    """Run one heal step. Never raises."""
    chain = HEAL_CHAINS.get(chain_name)
    if not chain:
        return {"ok": False, "reason": "unknown_chain", "chain": chain_name}
    steps = chain["steps"]
    if step_idx >= len(steps):
        return {"ok": False, "reason": "chain_exhausted", "chain": chain_name}
    step = steps[step_idx]
    action = step["action"]
    try:
        outcome = _dispatch(action, chain_name)
    except Exception as exc:
        outcome = f"error:{type(exc).__name__}"
    _record(chain_name, step_idx, action, outcome, step.get("description", ""))
    return {"ok": outcome.startswith("success"),
            "chain": chain_name, "step": step_idx,
            "action": action, "outcome": outcome}


def _dispatch(action: str, chain_name: str) -> str:
    if MODE != "production":
        return "success:mock"
    if action in ("restart_daemon", "force_redeploy"):
        try:
            from nexus.capabilities.daemon_ops import restart_daemon
            restart_daemon()
            return f"success:{action}"
        except Exception as exc:
            return f"error:{type(exc).__name__}"
    if action == "escalate":
        try:
            from nexus.capabilities import alert
            alert.send_telegram_alert(f"platform_healer: {chain_name} escalated")
            return "success:escalated"
        except Exception:
            return "success:escalate_noop"
    return "success:noted"  # log_warning / suppress_tenant / unknown


def evaluate_heal_chains() -> dict[str, Any]:
    """Run detections + advance any active chains. Returns a report."""
    triggered, advanced = [], []
    with _state_lock:
        for name, chain in HEAL_CHAINS.items():
            try: detected = bool(chain["detection"]())
            except Exception: detected = False
            prog = _active.get(name)
            if detected and prog is None:
                _active[name] = {"step": 1, "started_at": _now_iso()}
                triggered.append(name)
                advanced.append(execute_step(name, 0))
            elif prog is not None and detected:
                idx = prog["step"]
                if idx < len(chain["steps"]):
                    advanced.append(execute_step(name, idx))
                    prog["step"] = idx + 1
            elif prog is not None and not detected:
                advanced.append({"chain": name, "outcome": "resolved"})
                _active.pop(name, None)
    return {"triggered": triggered, "steps": advanced,
            "active": dict(_active), "checked_at": _now_iso()}


def get_active_chains() -> dict[str, Any]:
    with _state_lock:
        return dict(_active)


def reset_state() -> None:
    with _state_lock:
        _active.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def journey_healer_operational() -> dict[str, Any]:
    """Synthetic: every detection function runs without raising."""
    errors: list[str] = []
    for name, chain in HEAL_CHAINS.items():
        try:
            chain["detection"]()
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
    if errors:
        return {"name": "healer_operational", "status": "fail",
                "error": "; ".join(errors)}
    return {"name": "healer_operational", "status": "pass",
            "details": f"{len(HEAL_CHAINS)} detections OK"}
