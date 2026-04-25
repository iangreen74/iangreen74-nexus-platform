"""In-memory store for NEXUS_MODE != 'production'.

Used by tests and local development. Real Postgres + Neptune writers
short-circuit to this store when MODE is not production. Pattern mirrors
nexus/overwatch_graph.py:_local_store.
"""
from __future__ import annotations

import threading
from typing import Any

# Keys: object_type ('EngineeringTask', etc) and 'edges'.
# Values: list[dict] for objects, list[dict] for edges with shape
#         {edge_type, from_id, from_type, to_id, to_type, properties, created_at}.
_local_store: dict[str, list[dict[str, Any]]] = {}
_lock = threading.RLock()


def reset() -> None:
    """Clear all in-memory state. Tests call this between cases."""
    with _lock:
        _local_store.clear()


def append_object(row: dict) -> None:
    with _lock:
        _local_store.setdefault(row["object_type"], []).append(row)


def append_edge(edge: dict) -> None:
    with _lock:
        _local_store.setdefault("__edges__", []).append(edge)


def list_by_type(object_type: str) -> list[dict]:
    with _lock:
        return list(_local_store.get(object_type, []))


def list_edges() -> list[dict]:
    with _lock:
        return list(_local_store.get("__edges__", []))


def find_object(object_id: str, version: int | None = None) -> dict | None:
    """Return the matching version row, or current (valid_to is None) if version is None."""
    with _lock:
        for object_type, rows in _local_store.items():
            if object_type.startswith("__"):
                continue
            for r in rows:
                if r.get("id") != object_id:
                    continue
                if version is None and r.get("valid_to") is None:
                    return r
                if version is not None and r.get("version_id") == version:
                    return r
    return None


def list_versions_for(object_id: str) -> list[dict]:
    with _lock:
        out: list[dict] = []
        for object_type, rows in _local_store.items():
            if object_type.startswith("__"):
                continue
            out.extend(r for r in rows if r.get("id") == object_id)
        return sorted(out, key=lambda r: r.get("version_id", 0))


def supersede_prior_version(object_id: str, valid_to: str) -> None:
    """Mark the current version (valid_to is None) as superseded."""
    with _lock:
        for rows in _local_store.values():
            if not isinstance(rows, list):
                continue
            for r in rows:
                if r.get("id") == object_id and r.get("valid_to") is None:
                    r["valid_to"] = valid_to
                    return
