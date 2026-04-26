"""Phase 0c cross-tenant read guardrails (3 of 3).

Same-account multi-tenant architecture means α/β/γ IAM paths deliver
equivalently strong security boundaries on actual implementation.
Path γ chosen: existing overwatch-v2-reasoner-role + naming-based filter
in tool code. These guardrails are detection-in-depth, addressing the
class of failure the April 24 incident exposed (tool code as the sole
enforcement layer).

Guardrails:
  1. _validate_tenant_id  — fail-closed, mandatory non-empty forge-* prefix
  2. _assert_tenant_scoped — runtime assertion that returned resources
                             match the requested tenant's naming pattern
  3. _audit_cross_tenant_call — append-only structured audit record
                                to /overwatch-v2/cross-tenant-audit

Spec: docs/OPERATIONAL_TRUTH_SUBSTRATE.md Phase 0c.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterable

log = logging.getLogger("nexus.overwatch_v2.cross_tenant")

AUDIT_LOG_GROUP = "/overwatch-v2/cross-tenant-audit"
TENANT_PREFIX = "forge-"
RESOURCE_NAME_PREFIX_TEMPLATE = "forgescaler-forge-{short}-"
SHORT_ID_LENGTH = 7


class CrossTenantLeakageError(AssertionError):
    """Raised when a tool's filter logic returned a resource from a
    different tenant. Loud and intentionally not subclassed from
    ToolUnknown — leakage is a contract violation, not a transient error.
    """


def _validate_tenant_id(tenant_id: str) -> str:
    """Returns the 7-char short ID or raises ValueError. Fail-closed.

    Examples:
      'forge-1dba4143ca24ed1f' -> '1dba414'
      ''                        -> ValueError
      'foo'                     -> ValueError (no forge- prefix)
      'forge-12'                -> ValueError (too short for short ID)
    """
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError("tenant_id required (non-empty string)")
    if not tenant_id.startswith(TENANT_PREFIX):
        raise ValueError(
            f"tenant_id must start with {TENANT_PREFIX!r}, got: {tenant_id!r}"
        )
    short = tenant_id[len(TENANT_PREFIX):][:SHORT_ID_LENGTH]
    if len(short) < SHORT_ID_LENGTH:
        raise ValueError(
            f"tenant_id too short for short-form naming "
            f"(need >= {SHORT_ID_LENGTH} chars after prefix): {tenant_id!r}"
        )
    return short


def _expected_resource_prefix(tenant_id: str) -> str:
    """Convenience for tests + tools that filter by name prefix."""
    return RESOURCE_NAME_PREFIX_TEMPLATE.format(short=_validate_tenant_id(tenant_id))


def _assert_tenant_scoped(
    resources: Iterable[dict],
    tenant_id: str,
    resource_field: str = "name",
) -> None:
    """Raises CrossTenantLeakageError if any resource's name doesn't match
    the tenant's expected prefix. The check is unconditional — even an
    empty list is valid (just means "no resources for this tenant").
    """
    expected = _expected_resource_prefix(tenant_id)
    for r in resources:
        name = (r or {}).get(resource_field, "")
        if not isinstance(name, str) or not name.startswith(expected):
            raise CrossTenantLeakageError(
                f"CROSS-TENANT LEAKAGE: tool requested tenant_id={tenant_id} "
                f"but found resource {name!r} which does not start with "
                f"{expected!r}. Refusing to return data."
            )


def _audit_cross_tenant_call(
    tenant_id: str,
    tool_name: str,
    resources_read: list[str] | None = None,
    result_count: int = 0,
    error: str | None = None,
) -> None:
    """Append-only audit record. Audit failure is non-fatal — emit a
    structured stderr warning but never raise into the calling tool.
    """
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "tool_name": tool_name,
        "resources_read": (resources_read or [])[:10],
        "result_count": int(result_count),
    }
    if error is not None:
        record["error"] = str(error)[:500]
    try:
        from nexus.aws_client import _client as factory
        logs = factory("logs")
        try:
            logs.put_log_events(
                logGroupName=AUDIT_LOG_GROUP,
                logStreamName=tenant_id,
                logEvents=[{
                    "timestamp": int(time.time() * 1000),
                    "message": json.dumps(record),
                }],
            )
        except logs.exceptions.ResourceNotFoundException:
            # Stream doesn't exist yet — create then retry once.
            logs.create_log_stream(
                logGroupName=AUDIT_LOG_GROUP,
                logStreamName=tenant_id,
            )
            logs.put_log_events(
                logGroupName=AUDIT_LOG_GROUP,
                logStreamName=tenant_id,
                logEvents=[{
                    "timestamp": int(time.time() * 1000),
                    "message": json.dumps(record),
                }],
            )
    except Exception as e:
        sys.stderr.write(
            f"AUDIT_WARN: failed to write cross-tenant audit "
            f"for tenant={tenant_id} tool={tool_name}: {e}\n"
        )
