"""Evidence query executor for the Phase 0e.2 report engine.

For each ``EvidenceQuery`` declared on an OperatorFeature, run the
declarative query against the source picked by ``kind`` and return
a ``QueryResult`` tagged with the original ``section_kind`` so
renderers (Echo tool 0e.3, Reports panel 0e.5) know whether to
render the rows as metric/table/list/text.

Implemented kinds: ``CLOUDWATCH_LOGS``, ``NEPTUNE_CYPHER``,
``POSTGRES_QUERY``. Stubs (return ``QueryResult`` with
``error="kind not yet implemented"`` and empty rows):
``CLOUDTRAIL_LOOKUP``, ``ALB_ACCESS_LOGS``, ``S3_LISTING``,
``ECS_DESCRIBE``. Stubs are pluggable — fill in by adding to
``_QUERY_HANDLERS``.

Tenant substitution: when ``query.accepts_tenant_id`` is True, spec
strings (e.g. SQL ``query`` or Cypher ``cypher``) get a ``{tenant_id}``
placeholder substituted with the runtime tenant.

Per-query exceptions are caught and converted to a QueryResult with
``error`` populated; the loop continues for the rest of the queries.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

import boto3

from nexus.operator_features.evidence import EvidenceQuery, EvidenceQueryKind
from nexus.operator_features.report import QueryResult
from nexus.operator_features.schema import OperatorFeature

logger = logging.getLogger(__name__)

_AWS_REGION = "us-east-1"
_DEFAULT_WINDOW_SECONDS = 1800  # 30 min — looser than signal-eval default


def execute_evidence_queries(
    feature: OperatorFeature,
    tenant_id: str = "_fleet",
) -> list[QueryResult]:
    """Run every EvidenceQuery on a feature."""
    return [_execute_one(q, tenant_id) for q in feature.evidence_queries]


def _execute_one(query: EvidenceQuery, tenant_id: str) -> QueryResult:
    """Single-query dispatch with universal exception handling."""
    handler = _QUERY_HANDLERS.get(query.kind)
    if handler is None:
        return QueryResult(
            name=query.name,
            kind=query.kind.value,
            section_kind=query.section_kind,
            error=f"kind not yet implemented: {query.kind.value}",
        )
    spec = _substitute_tenant(query.spec, tenant_id) if query.accepts_tenant_id else query.spec
    try:
        rows = handler(spec, query.max_results, query.freshness_window_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("evidence query failed: name=%s kind=%s err=%s",
                       query.name, query.kind, exc)
        return QueryResult(
            name=query.name,
            kind=query.kind.value,
            section_kind=query.section_kind,
            error=f"{type(exc).__name__}: {exc}",
        )
    rows = rows[:query.max_results]
    return QueryResult(
        name=query.name,
        kind=query.kind.value,
        section_kind=query.section_kind,
        rows=rows,
        row_count=len(rows),
    )


def _substitute_tenant(spec: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    """Replace ``{tenant_id}`` in any string-valued spec field."""
    out: dict[str, Any] = {}
    for k, v in spec.items():
        if isinstance(v, str) and "{tenant_id}" in v:
            out[k] = v.replace("{tenant_id}", tenant_id)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Per-kind query handlers
# ---------------------------------------------------------------------------

def _exec_cloudwatch_logs(
    spec: dict[str, Any], max_results: int, freshness_seconds: int | None,
) -> list[dict[str, Any]]:
    """Spec: log_group, filter_pattern, (window_seconds). Returns ts/msg rows."""
    logs = boto3.client("logs", region_name=_AWS_REGION)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    window = (
        spec.get("window_seconds")
        or freshness_seconds
        or _DEFAULT_WINDOW_SECONDS
    )
    start_ms = end_ms - window * 1000
    resp = logs.filter_log_events(
        logGroupName=spec["log_group"],
        startTime=start_ms,
        endTime=end_ms,
        filterPattern=spec.get("filter_pattern", ""),
        limit=max_results,
    )
    return [
        {
            "timestamp": _fmt_ts(e.get("timestamp")),
            "log_stream": e.get("logStreamName", ""),
            "message": (e.get("message") or "").rstrip("\n"),
        }
        for e in (resp.get("events") or [])
    ]


def _fmt_ts(ms: int | None) -> str:
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _exec_neptune_cypher(
    spec: dict[str, Any], max_results: int, freshness_seconds: int | None,
) -> list[dict[str, Any]]:
    """Spec: cypher, (parameters). Calls overwatch_graph.query."""
    from nexus import overwatch_graph
    cypher = spec["cypher"]
    parameters = spec.get("parameters") or {}
    rows = overwatch_graph.query(cypher, parameters)
    return rows


def _exec_postgres_query(
    spec: dict[str, Any], max_results: int, freshness_seconds: int | None,
) -> list[dict[str, Any]]:
    """Spec: target ('v1'|'v2', default 'v1'), query (SQL → rows)."""
    from nexus.operator_features._pg import open_pg_connection
    target = spec.get("target", "v1")
    sql = spec["query"]
    with open_pg_connection(target) as conn, conn.cursor() as cur:
        cur.execute(sql)
        if cur.description is None:
            return []
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany(max_results)
    return [dict(zip(cols, _row_to_jsonable(r))) for r in rows]


def _row_to_jsonable(row: tuple) -> tuple:
    """Convert non-JSON-friendly column values (datetimes, Decimals) to str."""
    out = []
    for v in row:
        if v is None or isinstance(v, (str, int, float, bool)):
            out.append(v)
        else:
            out.append(str(v))
    return tuple(out)


_QueryHandler = Callable[
    [dict[str, Any], int, int | None], list[dict[str, Any]]
]

_QUERY_HANDLERS: dict[EvidenceQueryKind, _QueryHandler] = {
    EvidenceQueryKind.CLOUDWATCH_LOGS: _exec_cloudwatch_logs,
    EvidenceQueryKind.NEPTUNE_CYPHER: _exec_neptune_cypher,
    EvidenceQueryKind.POSTGRES_QUERY: _exec_postgres_query,
    # Stubs — adding to this dict enables the kind:
    # EvidenceQueryKind.CLOUDTRAIL_LOOKUP, ALB_ACCESS_LOGS, S3_LISTING, ECS_DESCRIBE
}


__all__ = ["execute_evidence_queries"]
