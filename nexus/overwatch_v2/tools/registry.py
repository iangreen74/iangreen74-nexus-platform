"""Overwatch V2 tool registry — substrate for every reasoner-callable tool.

Every tool the V2 reasoner can invoke (read or mutation) registers here.
`dispatch()` is the single execution chokepoint: validates parameters,
gates mutations behind approval-token verification, runs the handler,
emits an audit event. No caller bypasses this surface.

Mirrors `nexus/capabilities/registry.py` for V1, adapted for V2's tool
surface (spec §5.4): JSON-schema parameter contracts, Bedrock Converse
tool-array shape, single-use approval tokens (spec §5.5 / §9.3).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from nexus.config import MODE

log = logging.getLogger("nexus.overwatch_v2.tools")

RISK_LOW, RISK_MEDIUM, RISK_HIGH = "low", "medium", "high"
_VALID_RISK = frozenset({RISK_LOW, RISK_MEDIUM, RISK_HIGH})


class ToolNotFound(KeyError):
    """dispatch() called with an unregistered tool name."""


class ParameterValidationError(ValueError):
    """Tool parameters failed schema validation."""


class ApprovalRequired(PermissionError):
    """Mutation tool dispatched without an approval_token."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameter_schema: dict
    handler: Callable[..., Any]
    requires_approval: bool
    risk_level: str

    def __post_init__(self) -> None:
        if self.risk_level not in _VALID_RISK:
            raise ValueError(
                f"risk_level must be one of {sorted(_VALID_RISK)}, "
                f"got {self.risk_level!r}"
            )


@dataclass
class ToolResult:
    ok: bool
    value: Any = None
    error: Optional[str] = None
    duration_ms: int = 0
    audit_id: Optional[str] = None


_registry: dict[str, ToolSpec] = {}
_local_audit_log: list[dict[str, Any]] = []
_lock = threading.Lock()


def _reset_registry_for_tests() -> None:
    """Clear registry + audit log. Tests only."""
    with _lock:
        _registry.clear()
        _local_audit_log.clear()


def register(spec: ToolSpec) -> None:
    """Idempotently add a tool. Re-registering the same name overwrites."""
    with _lock:
        _registry[spec.name] = spec
    log.info("registered tool %s (risk=%s, mut=%s)",
             spec.name, spec.risk_level, spec.requires_approval)


def get_spec(name: str) -> ToolSpec:
    spec = _registry.get(name)
    if spec is None:
        raise ToolNotFound(name)
    return spec


def list_tools(include_mutations: bool = True) -> list[dict[str, Any]]:
    """Return tools shaped for Bedrock Converse `tools` array (toolSpec form)."""
    out: list[dict[str, Any]] = []
    for spec in _registry.values():
        if not include_mutations and spec.requires_approval:
            continue
        out.append({"toolSpec": {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": {"json": spec.parameter_schema},
        }})
    return out


_TYPE_MAP = {
    "string": str, "integer": int, "number": (int, float),
    "boolean": bool, "array": list, "object": dict,
}


def parameter_validate(schema: dict, params: dict) -> Optional[str]:
    """JSON-schema-lite. None if valid, error string if not."""
    if not isinstance(params, dict):
        return f"parameters must be a dict, got {type(params).__name__}"
    props = schema.get("properties", {}) or {}
    for key in schema.get("required", []) or []:
        if key not in params:
            return f"missing required parameter: {key}"
    for key, value in params.items():
        if key not in props:
            if schema.get("additionalProperties") is False:
                return f"unexpected parameter: {key}"
            continue
        spec = props[key] or {}
        expected = spec.get("type")
        if expected and expected in _TYPE_MAP:
            if not isinstance(value, _TYPE_MAP[expected]):
                return (f"parameter {key!r} expected {expected}, "
                        f"got {type(value).__name__}")
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            return f"parameter {key!r} must be one of {enum}, got {value!r}"
    return None


def _emit_audit(name: str, params: dict, result: ToolResult,
                actor: str, token_id: Optional[str]) -> str:
    """Local mode: in-memory list. Production: overwatch_v2.action_events row."""
    audit_id = f"act-{int(time.time() * 1000)}-{name}"
    record = {
        "audit_id": audit_id, "tool_name": name, "actor": actor,
        "parameters": params, "ok": result.ok, "error": result.error,
        "duration_ms": result.duration_ms,
        "approval_token_id": token_id,
        "ts_unix_ms": int(time.time() * 1000),
    }
    if MODE != "production":
        with _lock:
            _local_audit_log.append(record)
        return audit_id
    try:
        from nexus.overwatch_v2.audit import emit_action_event  # type: ignore
        emit_action_event(record)
    except Exception:
        log.exception("action_events emit failed for %s", name)
    return audit_id


def dispatch(
    name: str,
    parameters: dict,
    approval_token: Optional[str] = None,
    actor: str = "reasoner",
) -> ToolResult:
    """Validate, gate (verify token + audit), execute, audit. Returns ToolResult."""
    spec = get_spec(name)
    err = parameter_validate(spec.parameter_schema, parameters)
    if err is not None:
        raise ParameterValidationError(err)
    token_id: Optional[str] = None
    if spec.requires_approval:
        from nexus.overwatch_v2.tools._approval_gate import precheck
        ok, reason, token_id = precheck(name, parameters, approval_token, actor)
        if not ok:
            raise ApprovalRequired(f"tool {name!r}: {reason}")
    started = time.perf_counter()
    result = ToolResult(ok=False)
    try:
        value = spec.handler(**parameters)
        result.ok = True
        result.value = value
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        log.exception("tool %s failed", name)
    finally:
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        result.audit_id = _emit_audit(name, parameters, result, actor, token_id)
        if spec.requires_approval:
            from nexus.overwatch_v2.tools._approval_gate import emit_outcome
            emit_outcome(name, parameters, actor, result.ok, result.error,
                         result.duration_ms, token_id)
    return result


def get_local_audit_log() -> list[dict[str, Any]]:
    """Test helper — snapshot of the local audit log."""
    with _lock:
        return list(_local_audit_log)
