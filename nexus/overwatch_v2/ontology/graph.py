"""V2 Neptune Analytics writer — targets the V2 graph (g-279kpnulx0).

Distinct from nexus/neptune_client.py (which targets g-1xwjj34141).
Uses a separately-cached boto3 client pointed at the V2 graph identifier.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from nexus.overwatch_v2.ontology.exceptions import V2GraphWriteError

log = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
V2_GRAPH_ID = os.environ.get("OVERWATCH_V2_GRAPH_ID", "g-279kpnulx0")

_client_singleton = None


def _client():
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    import boto3
    from botocore.config import Config
    cfg = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 1})
    endpoint = f"https://{AWS_REGION}.neptune-graph.amazonaws.com"
    _client_singleton = boto3.client(
        "neptune-graph", region_name=AWS_REGION, endpoint_url=endpoint, config=cfg,
    )
    log.info("V2 Neptune client ready (graph=%s)", V2_GRAPH_ID)
    return _client_singleton


def query(cypher: str, parameters: dict | None = None) -> list[dict[str, Any]]:
    """Execute openCypher against the V2 graph. Returns parsed result rows."""
    try:
        resp = _client().execute_query(
            graphIdentifier=V2_GRAPH_ID,
            queryString=cypher,
            parameters=parameters or {},
            language="OPEN_CYPHER",
        )
        payload = json.loads(resp["payload"].read())
        return payload.get("results", []) or []
    except Exception as e:
        log.exception("V2 Neptune query failed")
        raise V2GraphWriteError(f"Neptune query failed: {e}") from e


_MERGE_OBJECT = (
    "MERGE (n:{label} {{id: $id}}) "
    "SET n += $props "
    "RETURN n.id AS id, n.version_id AS version_id"
)

_MERGE_EDGE = (
    "MATCH (a {{id: $from_id}}) "
    "MATCH (b {{id: $to_id}}) "
    "MERGE (a)-[r:{etype} {{id: $edge_id}}]->(b) "
    "SET r += $props "
    "RETURN r.id AS edge_id"
)


def merge_object(label: str, props: dict) -> dict:
    """MERGE a node by id. Returns {id, version_id}."""
    rows = query(_MERGE_OBJECT.format(label=label),
                 {"id": props["id"], "props": props})
    if not rows:
        return {"id": props.get("id"), "version_id": props.get("version_id")}
    return {
        "id": rows[0].get("id", props.get("id")),
        "version_id": rows[0].get("version_id", props.get("version_id")),
    }


def merge_edge(edge_type: str, from_id: str, to_id: str,
               edge_id: str, properties: dict | None = None) -> dict:
    """MERGE a typed edge between two nodes. Returns {edge_id}."""
    rows = query(_MERGE_EDGE.format(etype=edge_type), {
        "from_id": from_id, "to_id": to_id,
        "edge_id": edge_id, "props": properties or {},
    })
    return {"edge_id": rows[0].get("edge_id", edge_id) if rows else edge_id}


def read_object(object_id: str) -> dict | None:
    rows = query(
        "MATCH (n {id: $id}) RETURN properties(n) AS props LIMIT 1",
        {"id": object_id},
    )
    if not rows:
        return None
    props = rows[0].get("props") or rows[0]
    return props if isinstance(props, dict) else None


def list_by_label(label: str, limit: int = 100) -> list[dict]:
    return query(
        f"MATCH (n:{label}) RETURN properties(n) AS props LIMIT $limit",
        {"limit": limit},
    )
