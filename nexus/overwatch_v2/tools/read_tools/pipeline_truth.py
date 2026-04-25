"""Tool 4 — query_pipeline_truth: wraps Track G's /api/v2/pipeline-truth endpoints.

Internal HTTP call. The whole point is the reasoner gets to call this as a
tool with a JSON schema rather than reasoning about how to make HTTP calls.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolForbidden, ToolNotFound, ToolThrottled, ToolUnknown,
)


BASE_URL_ENV = "OVERWATCH_V2_API_URL"
# FastAPI binds 9001 in this container (aria-console task def, port mapping
# 9001->9001; CLAUDE.md "Console: platform.vaultscaler.com (port 9001)").
# The reasoner runs in the same container, so localhost:9001 is the
# correct internal target. Override via OVERWATCH_V2_API_URL when the
# reasoner is split out into its own service.
DEFAULT_BASE_URL = "http://localhost:9001"

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["list_executions", "execution_detail", "regional_quotas"],
        },
        "execution_arn": {"type": "string",
                          "description": "Required when operation=execution_detail."},
        "state_machine_name": {"type": "string",
                               "description": "Defaults to forgewing-deploy-v2."},
        "after": {"type": "string", "description": "ISO-8601 datetime (list_executions)."},
        "before": {"type": "string", "description": "ISO-8601 datetime (list_executions)."},
        "limit": {"type": "integer", "description": "default 50, max 200"},
    },
    "required": ["operation"],
}


def _base_url() -> str:
    return os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).rstrip("/")


def _check_status(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    if resp.status_code in (401, 403):
        raise ToolForbidden(f"pipeline-truth {resp.status_code}: {resp.text[:200]}")
    if resp.status_code == 404:
        raise ToolNotFound(f"pipeline-truth 404: {resp.text[:200]}")
    if resp.status_code == 429:
        raise ToolThrottled(f"pipeline-truth 429: {resp.text[:200]}")
    raise ToolUnknown(f"pipeline-truth {resp.status_code}: {resp.text[:200]}")


def handler(**params: Any) -> dict:
    op = params["operation"]
    base = _base_url()
    limit = max(1, min(int(params.get("limit") or 50), 200))
    try:
        with httpx.Client(timeout=15) as c:
            if op == "list_executions":
                q: dict[str, Any] = {"limit": limit}
                if params.get("state_machine_name"):
                    q["state_machine_name"] = params["state_machine_name"]
                if params.get("after"):
                    q["after"] = params["after"]
                if params.get("before"):
                    q["before"] = params["before"]
                r = c.get(f"{base}/api/v2/pipeline-truth/executions", params=q)
            elif op == "execution_detail":
                arn = params.get("execution_arn")
                if not arn:
                    raise ToolUnknown("execution_detail requires `execution_arn`")
                r = c.get(f"{base}/api/v2/pipeline-truth/executions/{arn}")
            elif op == "regional_quotas":
                r = c.get(f"{base}/api/v2/pipeline-truth/quotas")
            else:
                raise ToolUnknown(f"unknown operation: {op!r}")
    except httpx.HTTPError as e:
        raise ToolUnknown(f"pipeline-truth HTTP error: {e}") from e
    _check_status(r)
    return r.json()


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="query_pipeline_truth",
        description=(
            "Query Track G's truth-first pipeline view "
            "(verdict-classified Step Functions executions plus regional quotas). "
            "Wraps /api/v2/pipeline-truth/* HTTP endpoints."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))
