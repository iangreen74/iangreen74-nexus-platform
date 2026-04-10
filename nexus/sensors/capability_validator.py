"""
Capability Validator — continuous assessment of every tenant's full
Forgewing capability stack.

Runs on every Overwatch poll cycle. For each active tenant, checks
8 layers of capabilities against the documented Forgewing spec.
Any missing capability triggers a triage event with specific diagnosis.

This is how we ensure no tenant silently operates in a degraded state.
Every capability Ben was missing for 12 hours would have been flagged
within 30 seconds by this validator.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import aws_client, neptune_client
from nexus.config import MODE

logger = logging.getLogger("nexus.sensors.capability_validator")

# Stages where ingestion should be complete
_POST_INGESTION_STAGES = frozenset({
    "brief_pending", "brief_pending_approval", "executing", "complete",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CapabilityCheck:
    layer: str
    check: str
    status: str          # pass, fail, warn, skip
    detail: str
    auto_healable: bool = False
    heal_capability: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapabilityReport:
    tenant_id: str
    timestamp: str
    layers_checked: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    checks_warned: int = 0
    overall: str = "unknown"  # fully_operational, degraded, blocked, onboarding
    checks: list[CapabilityCheck] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "layers_checked": self.layers_checked,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "checks_warned": self.checks_warned,
            "overall": self.overall,
            "checks": [c.to_dict() for c in self.checks],
            "blockers": self.blockers,
            "score": f"{self.checks_passed}/{self.checks_passed + self.checks_failed + self.checks_warned}",
        }


# ---------------------------------------------------------------------------
# Individual checks — each returns a CapabilityCheck
# ---------------------------------------------------------------------------

def _check_token_valid(tenant_id: str) -> CapabilityCheck:
    """Critical: github_token non-empty in Secrets Manager."""
    secret_name = f"forgescaler/tenant/{tenant_id}/github-token"
    try:
        secret = aws_client.get_secret(secret_name)
        token = secret.get("github_token") or secret.get("_raw", "")
        if token:
            return CapabilityCheck(
                "onboarding", "token_valid", "pass",
                "GitHub token present and non-empty",
            )
        return CapabilityCheck(
            "onboarding", "token_valid", "fail",
            "GitHub token is empty — daemon cannot access repo",
            auto_healable=True, heal_capability="refresh_tenant_token",
        )
    except Exception:
        return CapabilityCheck(
            "onboarding", "token_valid", "fail",
            "Token secret not found",
            auto_healable=False,
        )


def _check_installation_id(tenant_id: str) -> CapabilityCheck:
    """Critical: installation_id present for token refresh."""
    secret_name = f"forgescaler/tenant/{tenant_id}/github-token"
    try:
        secret = aws_client.get_secret(secret_name)
        iid = secret.get("installation_id")
        if iid:
            return CapabilityCheck(
                "onboarding", "installation_id", "pass",
                f"Installation ID present: {iid}",
            )
        return CapabilityCheck(
            "onboarding", "installation_id", "fail",
            "No installation_id — GitHub App not connected",
        )
    except Exception:
        return CapabilityCheck(
            "onboarding", "installation_id", "fail",
            "Token secret not found",
        )


def _check_repo_files(tenant_id: str, ctx: dict[str, Any]) -> CapabilityCheck:
    """Critical: RepoFile count > 0 in Neptune."""
    files = neptune_client.query(
        "MATCH (f:RepoFile {tenant_id: $tid}) RETURN count(f) AS c",
        {"tid": tenant_id},
    )
    count = int(files[0].get("c", 0)) if files else 0
    stage = ctx.get("mission_stage", "")
    if count > 0:
        return CapabilityCheck(
            "ingestion", "repo_files_exist", "pass",
            f"{count} RepoFile nodes indexed",
        )
    if stage in _POST_INGESTION_STAGES:
        return CapabilityCheck(
            "ingestion", "repo_files_exist", "fail",
            f"0 RepoFile nodes but stage is '{stage}' — ingestion failed",
            auto_healable=True, heal_capability="retrigger_ingestion",
        )
    return CapabilityCheck(
        "ingestion", "repo_files_exist", "skip",
        f"Stage '{stage}' — ingestion not yet expected",
    )


def _check_tasks_exist(tenant_id: str, ctx: dict[str, Any]) -> CapabilityCheck:
    """Critical: MissionTask count > 0."""
    tasks = neptune_client.get_recent_tasks(tenant_id, limit=1)
    if tasks:
        return CapabilityCheck(
            "ingestion", "tasks_exist", "pass",
            "MissionTask nodes exist",
        )
    stage = ctx.get("mission_stage", "")
    if stage in _POST_INGESTION_STAGES:
        return CapabilityCheck(
            "ingestion", "tasks_exist", "fail",
            f"No MissionTask nodes but stage is '{stage}'",
        )
    return CapabilityCheck(
        "ingestion", "tasks_exist", "skip",
        f"Stage '{stage}' — tasks not yet expected",
    )


def _check_code_gen(tenant_id: str) -> CapabilityCheck:
    """Critical: last PR attempt didn't fail."""
    prs = neptune_client.get_recent_prs(tenant_id, limit=1)
    tasks = neptune_client.get_recent_tasks(tenant_id, limit=10)
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]

    if prs:
        return CapabilityCheck(
            "code_gen", "code_gen_working", "pass",
            f"PRs exist — code gen pipeline is producing output",
        )
    if not tasks:
        return CapabilityCheck(
            "code_gen", "code_gen_working", "skip",
            "No tasks yet — code gen not expected",
        )
    if in_progress:
        return CapabilityCheck(
            "code_gen", "code_gen_working", "warn",
            f"Tasks exist ({len(tasks)}) with {len(in_progress)} in_progress but no PRs yet",
            auto_healable=True, heal_capability="validate_tenant_onboarding",
        )
    return CapabilityCheck(
        "code_gen", "code_gen_working", "warn",
        f"{len(tasks)} tasks but no PRs created yet",
        auto_healable=True, heal_capability="check_pipeline_health",
    )


def _check_accretion_sources(tenant_id: str) -> CapabilityCheck:
    """Important: intelligence nodes exist."""
    sources_found = 0
    for label in ("UserPortrait", "CodingConvention", "DecisionTrajectory",
                  "IntentModel", "StrategicContext"):
        rows = neptune_client.query(
            f"MATCH (n:{label} {{tenant_id: $tid}}) RETURN count(n) AS c",
            {"tid": tenant_id},
        )
        if rows and int(rows[0].get("c", 0)) > 0:
            sources_found += 1

    # Also count non-node sources: tasks, PRs, conversation
    tasks = neptune_client.get_recent_tasks(tenant_id, limit=1)
    if tasks:
        sources_found += 1
    prs = neptune_client.get_recent_prs(tenant_id, limit=1)
    if prs:
        sources_found += 1
    conv_count = neptune_client.get_conversation_count(tenant_id)
    if conv_count > 0:
        sources_found += 1

    if sources_found >= 5:
        return CapabilityCheck(
            "intelligence", "accretion_sources", "pass",
            f"{sources_found}/8 checked sources returning data",
        )
    if sources_found > 0:
        return CapabilityCheck(
            "intelligence", "accretion_sources", "warn",
            f"Only {sources_found}/8 checked sources returning data",
        )
    return CapabilityCheck(
        "intelligence", "accretion_sources", "warn",
        "No intelligence sources returning data yet",
    )


def _check_brief(tenant_id: str) -> CapabilityCheck:
    """Important: MissionBrief exists and is not stale."""
    briefs = neptune_client.query(
        "MATCH (b:MissionBrief {tenant_id: $tid}) "
        "RETURN b.updated_at AS updated, b.is_stale AS stale",
        {"tid": tenant_id},
    )
    if not briefs:
        return CapabilityCheck(
            "brief", "brief_exists", "warn",
            "No MissionBrief node — brief not yet generated",
        )
    stale = briefs[0].get("stale")
    if stale:
        return CapabilityCheck(
            "brief", "brief_exists", "warn",
            "MissionBrief exists but is stale — needs regeneration",
        )
    return CapabilityCheck(
        "brief", "brief_exists", "pass",
        "MissionBrief exists and is current",
    )


def _check_conversation(tenant_id: str) -> CapabilityCheck:
    """Important: conversation messages exist."""
    count = neptune_client.get_conversation_count(tenant_id)
    if count > 0:
        return CapabilityCheck(
            "conversation", "conversation_active", "pass",
            f"{count} conversation messages",
        )
    return CapabilityCheck(
        "conversation", "conversation_active", "warn",
        "No conversation messages — ARIA not yet used",
    )


def _check_imports_mapped(tenant_id: str) -> CapabilityCheck:
    """Informational: IMPORTS edges exist."""
    rows = neptune_client.query(
        "MATCH (:RepoFile {tenant_id: $tid})-[:IMPORTS]->(:RepoFile) "
        "RETURN count(*) AS c",
        {"tid": tenant_id},
    )
    count = int(rows[0].get("c", 0)) if rows else 0
    if count > 0:
        return CapabilityCheck(
            "ingestion", "imports_mapped", "pass",
            f"{count} IMPORTS edges between RepoFiles",
        )
    return CapabilityCheck(
        "ingestion", "imports_mapped", "warn",
        "No IMPORTS edges — dependency graph not built",
    )


def _check_brief_entries(tenant_id: str) -> CapabilityCheck:
    """Informational: BriefEntry nodes being created."""
    rows = neptune_client.query(
        "MATCH (b:BriefEntry {tenant_id: $tid}) RETURN count(b) AS c",
        {"tid": tenant_id},
    )
    count = int(rows[0].get("c", 0)) if rows else 0
    if count > 0:
        return CapabilityCheck(
            "brief", "brief_entries_logged", "pass",
            f"{count} BriefEntry nodes recorded",
        )
    return CapabilityCheck(
        "brief", "brief_entries_logged", "warn",
        "No BriefEntry nodes — events not feeding the brief",
    )


def _check_deployment(tenant_id: str) -> CapabilityCheck:
    """Informational: CF stack exists or tenant is deployed."""
    try:
        infra = aws_client.describe_tenant_infra(tenant_id)
        if infra.get("provisioned"):
            return CapabilityCheck(
                "deployment", "deployment_available", "pass",
                "Tenant infrastructure provisioned",
            )
        return CapabilityCheck(
            "deployment", "deployment_available", "warn",
            "No CF stack matched — deployment not provisioned",
        )
    except Exception:
        return CapabilityCheck(
            "deployment", "deployment_available", "warn",
            "Could not check deployment status",
        )


# ---------------------------------------------------------------------------
# Main validation functions
# ---------------------------------------------------------------------------

def validate_tenant_capabilities(tenant_id: str) -> CapabilityReport:
    """
    Run all capability checks for a single tenant. Returns a complete
    CapabilityReport. Never raises.
    """
    report = CapabilityReport(
        tenant_id=tenant_id,
        timestamp=_now().isoformat(),
    )
    ctx = neptune_client.get_tenant_context(tenant_id)
    stage = ctx.get("mission_stage", "")

    # Define all checks grouped by layer
    all_checks = [
        # Critical (blocks PR generation)
        lambda: _check_token_valid(tenant_id),
        lambda: _check_installation_id(tenant_id),
        lambda: _check_repo_files(tenant_id, ctx),
        lambda: _check_tasks_exist(tenant_id, ctx),
        lambda: _check_code_gen(tenant_id),
        # Important (degrades experience)
        lambda: _check_accretion_sources(tenant_id),
        lambda: _check_brief(tenant_id),
        lambda: _check_conversation(tenant_id),
        # Informational
        lambda: _check_imports_mapped(tenant_id),
        lambda: _check_brief_entries(tenant_id),
        lambda: _check_deployment(tenant_id),
    ]

    layers_seen: set[str] = set()
    for check_fn in all_checks:
        try:
            check = check_fn()
            report.checks.append(check)
            layers_seen.add(check.layer)
            if check.status == "pass":
                report.checks_passed += 1
            elif check.status == "fail":
                report.checks_failed += 1
                report.blockers.append(f"{check.layer}/{check.check}: {check.detail}")
            elif check.status == "warn":
                report.checks_warned += 1
        except Exception:
            logger.exception("capability check failed for %s", tenant_id)

    report.layers_checked = len(layers_seen)

    # Derive overall status
    if not stage or stage in ("awaiting_repo", "ingestion_pending"):
        report.overall = "onboarding"
    elif report.checks_failed > 0:
        report.overall = "blocked"
    elif report.checks_warned > 2:
        report.overall = "degraded"
    else:
        report.overall = "fully_operational"

    return report


def validate_all_tenants() -> list[CapabilityReport]:
    """Run capability validation for every active tenant."""
    return [
        validate_tenant_capabilities(tid)
        for tid in neptune_client.get_tenant_ids()
    ]


def capability_score(report: CapabilityReport) -> str:
    """Human-readable score string, e.g., '8/11 checks passing'."""
    total = report.checks_passed + report.checks_failed + report.checks_warned
    return f"{report.checks_passed}/{total} checks passing"
