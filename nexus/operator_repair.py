"""Surgeon #2 — repair-orphan-nodes.

Extracted from operator_actions.py to stay under the 200-line CI limit.
Uses _graph_query and _record_operator_action from operator_actions.
"""
from __future__ import annotations

from typing import Any

from nexus.operator_actions import _graph_query, _record_operator_action
from nexus.config import MODE

# Labels that legitimately use project_id — only these are repairable.
# Labels like OverwatchTenantSnapshot, CustomerActivity have null
# project_id by design and must NEVER be relabeled or purged.
PROJECT_SCOPED_LABELS = frozenset({
    "MissionTask", "MissionBrief", "BriefEntry", "AnalysisReport",
    "ConversationMessage", "RepoFile", "DeploymentProgress",
    "PredictedTask", "OmniscientInsight", "TrajectoryInsight",
    "IntentSnapshot",
})


def repair_orphan_nodes(
    tenant_id: str,
    target_project_id: str,
    labels_to_repair: list[str] | None = None,
    dry_run: bool = True,
    operator_id: str = "ian",
) -> dict[str, Any]:
    """Relabel orphan nodes to target_project_id. Label-scoped only.

    Dry-run by default — returns counts without mutating.
    """
    # 1. Verify Tenant
    tenant = _graph_query(
        "MATCH (t:Tenant {tenant_id: $tid}) RETURN t.tenant_id AS tid",
        {"tid": tenant_id},
    )
    if not tenant and MODE == "production":
        raise ValueError(f"Tenant {tenant_id} not found")

    # 2. Verify target Project
    proj = _graph_query(
        "MATCH (p:Project {project_id: $pid, tenant_id: $tid}) "
        "RETURN p.project_id AS pid",
        {"pid": target_project_id, "tid": tenant_id},
    )
    if not proj and MODE == "production":
        raise ValueError(
            f"Project {target_project_id} not found on tenant {tenant_id}"
        )

    # 3. Intersect with PROJECT_SCOPED_LABELS
    if labels_to_repair:
        valid = [l for l in labels_to_repair if l in PROJECT_SCOPED_LABELS]
        ignored = [l for l in labels_to_repair
                   if l not in PROJECT_SCOPED_LABELS]
    else:
        valid = sorted(PROJECT_SCOPED_LABELS)
        ignored = []

    # 4. Census per label (always label-scoped)
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
            "would_relabel": count, "from_patterns": patterns,
        }
        total += count

    # 5. Dry-run: return preview
    if dry_run:
        audit_id = _record_operator_action(
            "repair_orphan_nodes_dryrun", operator_id, tenant_id,
            {"dry_run": True, "total": total, "target": target_project_id},
        )
        return {
            "audit_id": audit_id, "dry_run": True,
            "per_label": per_label, "total_affected": total,
            "ignored_labels": ignored,
        }

    # 6. Execute relabels (label-scoped, never unscoped)
    for label in valid:
        if per_label[label]["would_relabel"] == 0:
            continue
        _graph_query(
            f"MATCH (n:{label} {{tenant_id: $tid}}) "
            "WHERE n.project_id IS NULL OR n.project_id = $tid "
            "SET n.project_id = $target "
            "RETURN count(n) AS updated",
            {"tid": tenant_id, "target": target_project_id},
        )

    # 7. Record audit
    audit_id = _record_operator_action(
        "repair_orphan_nodes", operator_id, tenant_id,
        {"dry_run": False, "total": total, "target": target_project_id,
         "per_label": {k: v["would_relabel"] for k, v in per_label.items()
                       if v["would_relabel"] > 0}},
        mutated_nodes=[f"{l}:{per_label[l]['would_relabel']}"
                       for l in valid
                       if per_label[l]["would_relabel"] > 0],
    )
    return {
        "audit_id": audit_id, "dry_run": False,
        "per_label": per_label, "total_affected": total,
        "ignored_labels": ignored,
    }
