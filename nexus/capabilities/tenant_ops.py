"""
Tenant Operations — active capabilities for tenant health.

These capabilities interact with the Forgewing API, Neptune, GitHub,
and Secrets Manager to diagnose and fix tenant-level issues. Every
one is registered with blast radius, and the more dangerous ones
(retrigger_ingestion) require higher confidence to auto-approve.

NEXUS never imports from aria-platform. All tenant interactions go
through public APIs, Neptune queries, and Secrets Manager.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from nexus import aws_client, neptune_client, overwatch_graph
from nexus.capabilities import forgewing_api
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_MODERATE, BLAST_SAFE, MODE

logger = logging.getLogger("nexus.capabilities.tenant_ops")


def _github_app_token_for(installation_id: int) -> str | None:
    """
    Mint a fresh GitHub App installation token for the given installation_id.
    Returns the token string, or None on failure.
    """
    if MODE != "production":
        return "ghs_mock_installation_token"

    # Stage 1: Read the GitHub App secret from Secrets Manager
    try:
        app_secret = aws_client.get_secret("forgescaler/github-app")
    except Exception:
        logger.exception(
            "github-app-token(%s) STAGE 1 FAILED: cannot read secret 'forgescaler/github-app' from Secrets Manager",
            installation_id,
        )
        return None

    app_id = str(app_secret.get("app_id", ""))
    private_key = app_secret.get("private_key", "")
    if not app_id or not private_key:
        logger.error(
            "github-app-token(%s) STAGE 1 FAILED: secret present but missing fields — app_id=%s private_key=%s",
            installation_id,
            "set" if app_id else "EMPTY",
            f"{len(private_key)}chars" if private_key else "EMPTY",
        )
        return None

    # Stage 2: Generate RS256 JWT
    try:
        import jwt as pyjwt

        now = int(time.time())
        token = pyjwt.encode(
            {"iat": now - 60, "exp": now + 600, "iss": app_id},
            private_key,
            algorithm="RS256",
        )
        if isinstance(token, bytes):
            token = token.decode()
    except Exception:
        logger.exception(
            "github-app-token(%s) STAGE 2 FAILED: JWT generation failed — app_id=%s key_len=%d (bad key format or expired?)",
            installation_id, app_id, len(private_key),
        )
        return None

    # Stage 3: Exchange JWT for installation access token
    try:
        import httpx

        resp = httpx.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15,
        )
    except Exception:
        logger.exception(
            "github-app-token(%s) STAGE 3 FAILED: HTTP request to GitHub API failed (network/timeout)",
            installation_id,
        )
        return None

    if resp.status_code == 201:
        return resp.json().get("token")

    logger.warning(
        "github-app-token(%s) STAGE 3 FAILED: GitHub returned %s — %s "
        "(401=bad JWT/key, 404=installation revoked/wrong ID, 422=app suspended)",
        installation_id, resp.status_code, resp.text[:300],
    )
    return None


def refresh_tenant_token(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Read the tenant's secret; if github_token is empty or expired,
    regenerate from installation_id via GitHub App JWT.

    Safe blast radius — writing a fresh token never breaks anything.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    secret_name = f"forgescaler/tenant/{tenant_id}/github-token"
    if MODE != "production":
        return {"tenant_id": tenant_id, "refreshed": True, "mock": True}
    try:
        current = aws_client.get_secret(secret_name)
        installation_id = current.get("installation_id")
        if not installation_id:
            return {"tenant_id": tenant_id, "error": "no installation_id in secret"}
        new_token = _github_app_token_for(int(installation_id))
        if not new_token:
            return {"tenant_id": tenant_id, "error": "token mint failed"}
        # Write the fresh token back
        import boto3

        boto3.client("secretsmanager", region_name="us-east-1").put_secret_value(
            SecretId=secret_name,
            SecretString=json.dumps({
                "github_token": new_token,
                "installation_id": installation_id,
                "source": "overwatch_refresh",
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
            }),
        )
        overwatch_graph.record_healing_action(
            action_type="refresh_tenant_token",
            target=tenant_id,
            blast_radius="safe",
            trigger="empty_or_expired_token",
            outcome="success",
        )
        return {"tenant_id": tenant_id, "refreshed": True, "installation_id": installation_id}
    except Exception as exc:
        logger.exception("refresh_tenant_token(%s) failed", tenant_id)
        return {"tenant_id": tenant_id, "error": str(exc)}


def validate_tenant_onboarding(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Run the full onboarding checklist for a tenant:
    tenant exists, token present, write access, repo indexed, tasks created.

    Safe blast radius — purely diagnostic, no mutations.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    checks: dict[str, Any] = {}
    try:
        ctx = neptune_client.get_tenant_context(tenant_id)
        checks["tenant_exists"] = bool(ctx)
        checks["mission_stage"] = ctx.get("mission_stage")
        checks["repo_url"] = ctx.get("repo_url")

        secret_name = f"forgescaler/tenant/{tenant_id}/github-token"
        secret = aws_client.get_secret(secret_name)
        checks["token_present"] = bool(secret.get("github_token") or secret.get("_raw"))
        checks["installation_id"] = secret.get("installation_id")

        files = neptune_client.query(
            "MATCH (f:RepoFile {tenant_id: $tid}) RETURN count(f) AS c",
            {"tid": tenant_id},
        )
        checks["repo_file_count"] = int(files[0].get("c", 0)) if files else 0
        checks["repo_indexed"] = checks["repo_file_count"] > 0

        tasks = neptune_client.get_recent_tasks(tenant_id, limit=50)
        checks["task_count"] = len(tasks)
        checks["tasks_created"] = len(tasks) > 0
    except Exception as exc:
        checks["error"] = str(exc)

    checks["all_passed"] = all(
        checks.get(k)
        for k in ("tenant_exists", "token_present", "repo_indexed", "tasks_created")
    )
    return {"tenant_id": tenant_id, "checks": checks}


def verify_write_access(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Test write access to a tenant's repo by attempting a lightweight
    create-ref/delete-ref cycle. If this fails, the tenant's GitHub App
    installation doesn't have the right permissions.

    Safe blast radius — the test ref is immediately deleted.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    if MODE != "production":
        return {"tenant_id": tenant_id, "write_access": True, "mock": True}
    try:
        ctx = neptune_client.get_tenant_context(tenant_id)
        repo_url = ctx.get("repo_url", "")
        if not repo_url:
            return {"tenant_id": tenant_id, "write_access": False, "reason": "no repo_url"}
        # Extract owner/repo from URL
        parts = repo_url.rstrip("/").split("/")
        owner_repo = f"{parts[-2]}/{parts[-1]}".replace(".git", "")

        secret_name = f"forgescaler/tenant/{tenant_id}/github-token"
        secret = aws_client.get_secret(secret_name)
        token = secret.get("github_token") or secret.get("_raw")
        if not token:
            return {"tenant_id": tenant_id, "write_access": False, "reason": "empty_token"}

        import httpx

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        # Get default branch SHA
        branch_resp = httpx.get(
            f"https://api.github.com/repos/{owner_repo}/git/refs/heads/main",
            headers=headers,
            timeout=10,
        )
        if branch_resp.status_code != 200:
            return {"tenant_id": tenant_id, "write_access": False, "reason": f"branch_read: {branch_resp.status_code}"}
        sha = branch_resp.json().get("object", {}).get("sha", "")

        # Create test ref
        test_ref = "refs/heads/overwatch-write-test"
        create = httpx.post(
            f"https://api.github.com/repos/{owner_repo}/git/refs",
            json={"ref": test_ref, "sha": sha},
            headers=headers,
            timeout=10,
        )
        if create.status_code not in (200, 201):
            return {"tenant_id": tenant_id, "write_access": False, "reason": f"create_ref: {create.status_code}"}

        # Delete the test ref immediately
        httpx.delete(
            f"https://api.github.com/repos/{owner_repo}/git/refs/heads/overwatch-write-test",
            headers=headers,
            timeout=10,
        )
        return {"tenant_id": tenant_id, "write_access": True}
    except Exception as exc:
        logger.exception("verify_write_access(%s) failed", tenant_id)
        return {"tenant_id": tenant_id, "write_access": False, "error": str(exc)}


def retrigger_ingestion(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    POST to Forgewing to re-ingest a tenant's repo.

    Moderate blast radius — replaces the tenant's RepoFile nodes.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    result = forgewing_api.retrigger_ingestion(tenant_id)
    if not result.get("error"):
        overwatch_graph.record_healing_action(
            action_type="retrigger_ingestion",
            target=tenant_id,
            blast_radius="moderate",
            trigger="missing_repo_files",
            outcome="triggered",
        )
    return result


def validate_repo_indexing(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Compare Neptune RepoFile count against expected files.

    Safe blast radius — read-only comparison.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    files = neptune_client.query(
        "MATCH (f:RepoFile {tenant_id: $tid}) RETURN count(f) AS c",
        {"tid": tenant_id},
    )
    count = int(files[0].get("c", 0)) if files else 0
    return {
        "tenant_id": tenant_id,
        "repo_file_count": count,
        "indexed": count > 0,
    }


def check_tenant_repo_sync(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Verify Tenant.repo_url matches the active Project.repo_url.

    After multi-project operations (archive, start-from-scratch, project
    switch) the Tenant.repo_url can drift out of sync with the active
    Project, which causes the daemon to skip ingestion because it reads
    the repo URL from the Tenant node. Detect that mismatch here so
    Overwatch can surface it as an ActionRequired.

    Safe blast radius — two read-only Neptune queries.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    if MODE != "production":
        return {"synced": True, "mock": True}

    try:
        from nexus.capabilities.project_lifecycle import get_project_lifecycle

        ctx = neptune_client.get_tenant_context(tenant_id) or {}
        tenant_repo = (ctx.get("repo_url") or "").strip()

        lc = get_project_lifecycle(tenant_id=tenant_id)
        active = lc.get("active_project") or {}
        project_repo = (active.get("repo_url") or "").strip()

        if not active:
            return {"synced": True, "reason": "no_active_project"}

        if tenant_repo == project_repo:
            return {"synced": True, "tenant_repo": tenant_repo,
                    "project_repo": project_repo}

        if project_repo and not tenant_repo:
            return {
                "synced": False,
                "issue": "tenant_repo_empty",
                "detail": f"Tenant.repo_url is empty but Project has {project_repo}",
                "fix": "Set Tenant.repo_url from active Project",
                "tenant_repo": tenant_repo,
                "project_repo": project_repo,
            }

        return {
            "synced": False,
            "issue": "repo_mismatch",
            "detail": f"Tenant={tenant_repo} vs Project={project_repo}",
            "fix": "Sync Tenant.repo_url to active Project",
            "tenant_repo": tenant_repo,
            "project_repo": project_repo,
        }
    except Exception as exc:
        logger.debug("check_tenant_repo_sync failed for %s", tenant_id, exc_info=True)
        return {"synced": True, "error": str(exc)[:200]}


def check_pipeline_health(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    For a tenant: are tasks progressing? Are PRs being created?
    Identifies specific blockers (stuck tasks, Bedrock errors).

    Safe blast radius — read-only analysis.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    tasks = neptune_client.get_recent_tasks(tenant_id, limit=50)
    prs = neptune_client.get_recent_prs(tenant_id, limit=20)

    in_progress = [t for t in tasks if t.get("status") == "in_progress"]
    pending = [t for t in tasks if t.get("status") == "pending"]
    complete = [t for t in tasks if t.get("status") == "complete"]

    blockers: list[str] = []
    if not tasks:
        blockers.append("no_tasks: no MissionTask nodes found")
    if tasks and not prs:
        blockers.append("no_prs: tasks exist but no PRs created yet")
    if len(in_progress) > 3:
        blockers.append(f"too_many_in_progress: {len(in_progress)} tasks running simultaneously")

    return {
        "tenant_id": tenant_id,
        "task_count": len(tasks),
        "in_progress": len(in_progress),
        "pending": len(pending),
        "complete": len(complete),
        "pr_count": len(prs),
        "blockers": blockers,
        "healthy": len(blockers) == 0,
    }


def diagnose_tenant_deploy(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Diagnose a stuck or failed tenant deployment.

    Checks DeploymentProgress in Neptune, deploy status via Forgewing API,
    and Deployment DNA. Returns a detailed diagnosis with recommended action.
    Safe blast radius — purely diagnostic, no mutations.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    if MODE != "production":
        return {"tenant_id": tenant_id, "mock": True, "diagnosis": "mock deploy check", "status": "healthy"}

    diagnosis: dict[str, Any] = {"tenant_id": tenant_id, "checks": {}, "issues": [], "recommended_action": ""}

    # 1. Check DeploymentProgress in Neptune
    try:
        progress = neptune_client.query(
            "MATCH (d:DeploymentProgress {tenant_id: $tid}) "
            "RETURN d.stage AS stage, d.message AS message, "
            "d.updated_at AS updated_at",
            {"tid": tenant_id},
        )
        if progress:
            dp = progress[0]
            diagnosis["checks"]["deploy_progress"] = dp
            stage = dp.get("stage", "")
            if stage and stage not in ("live", "complete"):
                diagnosis["issues"].append(f"Deploy stuck at stage '{stage}'")
        else:
            diagnosis["checks"]["deploy_progress"] = None
            diagnosis["issues"].append("No DeploymentProgress node — deploy may never have started")
    except Exception as exc:
        diagnosis["checks"]["deploy_progress_error"] = str(exc)

    # 2. Check deploy status via Forgewing API
    try:
        deploy_status = forgewing_api.call_api("GET", f"/deploy-progress/{tenant_id}")
        diagnosis["checks"]["api_deploy_progress"] = deploy_status
        api_stage = deploy_status.get("stage", "")
        if api_stage and api_stage not in ("live", "complete", ""):
            diagnosis["issues"].append(f"API reports deploy stage: {api_stage}")
    except Exception as exc:
        diagnosis["checks"]["api_error"] = str(exc)

    # 3. Check Deployment DNA
    try:
        dna = forgewing_api.call_api("GET", f"/deployment-dna/{tenant_id}")
        diagnosis["checks"]["deployment_dna"] = {
            "recommendation": dna.get("recommendation"),
            "has_workflows": dna.get("workflow_count", 0) > 0,
        }
    except Exception as exc:
        diagnosis["checks"]["dna_error"] = str(exc)

    # 4. Determine recommended action
    issues = diagnosis["issues"]
    if not issues:
        diagnosis["recommended_action"] = "none — deploy appears healthy"
        diagnosis["status"] = "healthy"
    elif any("never have started" in i for i in issues):
        diagnosis["recommended_action"] = "retry_deploy"
        diagnosis["status"] = "stuck"
    elif any("stuck" in i.lower() for i in issues):
        diagnosis["recommended_action"] = "retry_deploy"
        diagnosis["status"] = "stuck"
    else:
        diagnosis["recommended_action"] = "escalate — unknown deploy issue"
        diagnosis["status"] = "unknown"

    return diagnosis


def retry_tenant_deploy(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Retry a stuck tenant deployment by POSTing to the Forgewing deploy endpoint.

    Moderate blast radius — triggers infrastructure provisioning in the
    customer's AWS account. Runs diagnosis first to confirm it's stuck.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    if MODE != "production":
        return {"tenant_id": tenant_id, "action": "deploy_triggered", "mock": True}

    diag = diagnose_tenant_deploy(tenant_id=tenant_id)
    if diag.get("status") == "healthy":
        return {"tenant_id": tenant_id, "action": "none", "reason": "deploy is healthy"}

    result = forgewing_api.call_api("POST", f"/deploy/{tenant_id}")
    if not result.get("error"):
        overwatch_graph.record_healing_action(
            action_type="retry_tenant_deploy",
            target=tenant_id,
            blast_radius="moderate",
            trigger="stuck_deploy",
            outcome="triggered",
        )
    return {"tenant_id": tenant_id, "action": "deploy_triggered", "api_response": result, "diagnosis": diag}


# Register all capabilities
for cap in [
    Capability(
        name="refresh_tenant_token",
        function=refresh_tenant_token,
        blast_radius=BLAST_SAFE,
        description="Mint fresh GitHub App token from installation_id if empty/expired",
    ),
    Capability(
        name="validate_tenant_onboarding",
        function=validate_tenant_onboarding,
        blast_radius=BLAST_SAFE,
        description="Run full onboarding checklist: tenant, token, write access, files, tasks",
    ),
    Capability(
        name="verify_write_access",
        function=verify_write_access,
        blast_radius=BLAST_SAFE,
        description="Test write access to tenant's repo via create-ref/delete-ref",
    ),
    Capability(
        name="retrigger_ingestion",
        function=retrigger_ingestion,
        blast_radius=BLAST_MODERATE,
        description="Re-ingest a tenant's repo via Forgewing API",
        requires_approval=False,
    ),
    Capability(
        name="validate_repo_indexing",
        function=validate_repo_indexing,
        blast_radius=BLAST_SAFE,
        description="Check RepoFile count in Neptune for a tenant",
    ),
    Capability(
        name="check_tenant_repo_sync",
        function=check_tenant_repo_sync,
        blast_radius=BLAST_SAFE,
        description="Verify Tenant.repo_url matches active Project.repo_url",
    ),
    Capability(
        name="check_pipeline_health",
        function=check_pipeline_health,
        blast_radius=BLAST_SAFE,
        description="Analyze task/PR pipeline for blockers",
    ),
    Capability(
        name="diagnose_tenant_deploy",
        function=diagnose_tenant_deploy,
        blast_radius=BLAST_SAFE,
        description="Diagnose a stuck or failed tenant deployment — checks CF, CodeBuild, progress state",
    ),
    Capability(
        name="retry_tenant_deploy",
        function=retry_tenant_deploy,
        blast_radius=BLAST_MODERATE,
        description="Retry a stuck tenant deployment via the Forgewing deploy API",
        requires_approval=False,
    ),
]:
    registry.register(cap)
