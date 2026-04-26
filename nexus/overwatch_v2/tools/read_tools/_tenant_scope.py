"""Phase 1 cross-tenant read primitive — three guardrails.

Path γ chosen (same-account, single shared `overwatch-v2-reasoner-role`).
Real tenant isolation is naming + tag-based, NOT IAM-enforced. These
three guardrails substitute detection-in-depth for IAM enforcement so
the same-account choice is auditable.

Guardrail 1 — fail-closed validation: every cross-tenant tool requires
a non-empty `tenant_id` matching `forge-XXXXXX...`. No fleet-wide reads
without an explicit separate flag.

Guardrail 2 — resource-tag assertion: every resource we touch must
carry `TenantId == tenant_id`. Resources without that tag (or with a
different value) raise `CrossTenantLeakage`. This catches filter bugs.

Guardrail 3 — audit log: every cross-tenant tool call writes one
JSON event to /overwatch-v2/cross-tenant-audit (CloudWatch). Schema:
{timestamp, tenant_id, tool_name, resources_read, result_count}.
Accidental cross-tenant reads become detectable in retrospect.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Iterable

log = logging.getLogger(__name__)

AUDIT_LOG_GROUP = "/overwatch-v2/cross-tenant-audit"
TENANT_ID_PATTERN = re.compile(r"^forge-[a-zA-Z0-9-]{6,}$")


class CrossTenantValidationError(ValueError):
    """Guardrail 1: tenant_id missing or malformed."""


class CrossTenantLeakage(AssertionError):
    """Guardrail 2: a tool returned a resource that doesn't belong to tenant_id."""


def require_tenant_id(tenant_id: str | None) -> str:
    """Guardrail 1. Returns the validated id or raises CrossTenantValidationError."""
    if not tenant_id or not isinstance(tenant_id, str):
        raise CrossTenantValidationError(
            "tenant_id is required and must be a non-empty string"
        )
    if not TENANT_ID_PATTERN.match(tenant_id):
        raise CrossTenantValidationError(
            f"tenant_id {tenant_id!r} does not match expected format "
            f"'forge-XXXXXXXXXXXXXXXX'"
        )
    return tenant_id


def assert_resource_belongs(
    tenant_id: str,
    resource_id: str,
    resource_tags: dict[str, str] | None,
) -> None:
    """Guardrail 2. Raise if the resource's TenantId tag doesn't match.

    A resource with NO TenantId tag at all is also rejected — Phase 1
    requires explicit tenant linkage. Untagged shared resources need
    an explicit allowlist if a tool genuinely needs them.
    """
    tags = resource_tags or {}
    actual = tags.get("TenantId") or tags.get("tenantId") or tags.get("tenant_id")
    if actual != tenant_id:
        raise CrossTenantLeakage(
            f"Cross-tenant leakage: requested {tenant_id!r} but "
            f"resource {resource_id!r} has TenantId={actual!r}"
        )


def list_tenant_resources(
    tenant_id: str,
    resource_type_filters: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return ARNs + tags for every resource tagged TenantId=<tenant_id>.

    Backed by the Resource Groups Tagging API (single call, paginated).
    `resource_type_filters` example: ["ecs:service", "ecs:cluster"].
    """
    require_tenant_id(tenant_id)
    from nexus.aws_client import _client
    client = _client("resourcegroupstaggingapi")
    kwargs: dict[str, Any] = {
        "TagFilters": [{"Key": "TenantId", "Values": [tenant_id]}],
    }
    if resource_type_filters:
        kwargs["ResourceTypeFilters"] = list(resource_type_filters)
    out: list[dict[str, Any]] = []
    pages = client.get_paginator("get_resources").paginate(**kwargs)
    for page in pages:
        for entry in page.get("ResourceTagMappingList", []) or []:
            tags = {t["Key"]: t["Value"] for t in entry.get("Tags", []) or []}
            out.append({"arn": entry.get("ResourceARN"), "tags": tags})
    return out


def write_audit_event(
    tenant_id: str,
    tool_name: str,
    resource_arns: list[str] | None = None,
    result_count: int = 0,
    extra: dict[str, Any] | None = None,
) -> None:
    """Guardrail 3. Write one JSON event to the audit log group.

    Best-effort — exceptions during audit-write are logged but never
    raised back to the caller (auditing must not break tool execution).
    """
    from nexus.aws_client import _client
    payload = {
        "timestamp": int(time.time() * 1000),
        "tenant_id": tenant_id,
        "tool_name": tool_name,
        "resources_read": resource_arns or [],
        "result_count": int(result_count),
    }
    if extra:
        payload["extra"] = extra
    stream_name = f"{tool_name}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    try:
        client = _client("logs")
        try:
            client.create_log_stream(
                logGroupName=AUDIT_LOG_GROUP, logStreamName=stream_name,
            )
        except client.exceptions.ResourceAlreadyExistsException:
            pass
        client.put_log_events(
            logGroupName=AUDIT_LOG_GROUP,
            logStreamName=stream_name,
            logEvents=[{
                "timestamp": payload["timestamp"],
                "message": json.dumps(payload),
            }],
        )
    except Exception as e:
        log.warning("cross-tenant audit write failed: %s", e)
