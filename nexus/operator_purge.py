"""Surgeon #4 — purge-orphan-nodes.

Complement to repair-orphan-nodes: where repair relabels, purge deletes.
Used when orphan nodes can't be safely relabeled (e.g., multi-project
tenant without clear provenance per orphan).

Critical design (from the 2026-04-24 forge-dogfood-runner incident):
- labels_to_purge is REQUIRED — no default-to-all.
- Every query is label-scoped: MATCH (n:<Label> ...). No bare node match.
- Non-project-scoped labels are silently dropped and reported as ignored.
- Dry-run is the default. Execution needs explicit dry_run=false.
"""
from __future__ import annotations

import logging
from typing import Any

from nexus.config import MODE
from nexus.operator_actions import _graph_query, _record_operator_action
from nexus.operator_repair import PROJECT_SCOPED_LABELS

log = logging.getLogger(__name__)


def purge_orphan_nodes(
    tenant_id: str,
    labels_to_purge: list[str],
    dry_run: bool = True,
    operator_id: str = "ian",
) -> dict[str, Any]:
    """DETACH DELETE orphan nodes for the given labels. Label-scoped only.

    Raises ValueError on missing tenant or empty labels_to_purge.
    """
    # 1. labels_to_purge REQUIRED — explicit-action-only.
    if not labels_to_purge:
        raise ValueError(
            "labels_to_purge is required and must be non-empty"
        )

    # 2. Verify Tenant
    tenant = _graph_query(
        "MATCH (t:Tenant {tenant_id: $tid}) RETURN t.tenant_id AS tid",
        {"tid": tenant_id},
    )
    if not tenant and MODE == "production":
        raise ValueError(f"Tenant {tenant_id} not found")

    # 3. Intersect with PROJECT_SCOPED_LABELS — drop anything else.
    valid = [l for l in labels_to_purge if l in PROJECT_SCOPED_LABELS]
    ignored = [l for l in labels_to_purge
               if l not in PROJECT_SCOPED_LABELS]

    # 4. Census per label — always label-scoped.
    per_label: dict[str, dict[str, Any]] = {}
    total = 0
    for label in valid:
        r_null = _graph_query(
            f"MATCH (n:{label} {{tenant_id: $tid}}) "
            "WHERE n.project_id IS NULL RETURN count(n) AS c",
            {"tid": tenant_id},
        )
        r_tid = _graph_query(
            f"MATCH (n:{label} {{tenant_id: $tid}}) "
            "WHERE n.project_id = $tid RETURN count(n) AS c",
            {"tid": tenant_id},
        )
        n_null = r_null[0].get("c", 0) if r_null else 0
        n_tid = r_tid[0].get("c", 0) if r_tid else 0
        count = n_null + n_tid
        patterns = []
        if n_null:
            patterns.append("null_pid")
        if n_tid:
            patterns.append("tid_fallback")
        per_label[label] = {
            "would_purge": count, "from_patterns": patterns,
        }
        total += count

    # 5. Dry-run: record audit and return preview — no deletion.
    if dry_run:
        audit_id = _record_operator_action(
            "purge_orphan_nodes_dryrun", operator_id, tenant_id,
            {"dry_run": True, "total": total,
             "labels": valid, "ignored": ignored},
        )
        return {
            "audit_id": audit_id, "dry_run": True,
            "per_label": per_label, "total_affected": total,
            "ignored_labels": ignored,
        }

    # 6. Execute — DETACH DELETE per label, label-scoped.
    executed: dict[str, int] = {}
    for label in valid:
        if per_label[label]["would_purge"] == 0:
            continue
        r = _graph_query(
            f"MATCH (n:{label} {{tenant_id: $tid}}) "
            "WHERE n.project_id IS NULL OR n.project_id = $tid "
            "WITH n DETACH DELETE n RETURN count(*) AS deleted",
            {"tid": tenant_id},
        )
        deleted = r[0].get("deleted", 0) if r else 0
        executed[label] = deleted
        # Divergence warning: dry-run count vs execute count.
        expected = per_label[label]["would_purge"]
        if deleted != expected:
            log.warning(
                "purge divergence on %s/%s: expected=%d deleted=%d",
                tenant_id[:12], label, expected, deleted,
            )

    # 7. Audit the mutation.
    audit_id = _record_operator_action(
        "purge_orphan_nodes", operator_id, tenant_id,
        {"dry_run": False, "total": total, "executed": executed,
         "ignored": ignored},
        mutated_nodes=[f"{l}:{executed[l]}" for l in executed],
    )
    return {
        "audit_id": audit_id, "dry_run": False,
        "per_label": per_label, "total_affected": total,
        "ignored_labels": ignored,
    }
