"""
Dogfood Capability — kicks off one automated deploy of a catalogue app.

Registered under the Capability registry as BLAST_DANGEROUS with
requires_approval=True. The runner activates when:
  1. A DogfoodConfig node has enabled=true (written by the UI batch button), OR
  2. An active DogfoodBatch exists (remaining > 0), OR
  3. DOGFOOD_ENABLED=true is set on the ECS environment (legacy fallback).

This capability only KICKS OFF a deploy. Polling for outcome lives in
`sensors/dogfood_sensor.py` and cleanup in `sensors/dogfood_reconciler.py`
so the daemon is never blocked waiting on a deploy to finish.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from nexus import overwatch_graph
from nexus.capabilities import forgewing_api
from nexus.capabilities.dogfood_catalogue import CATALOGUE, pick_app
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_DANGEROUS, GITHUB_SECRET_ID, MODE

logger = logging.getLogger("nexus.capabilities.dogfood")

GITHUB_API = "https://api.github.com"
GITHUB_USER = "iangreen74"
CIRCUIT_WINDOW = 10
CIRCUIT_MIN_SUCCESSES = 2


def _gh_token() -> str:
    """Read the GitHub PAT from Secrets Manager. Empty in local mode."""
    if MODE != "production":
        return ""
    try:
        from nexus.aws_client import get_secret
        secret = get_secret(GITHUB_SECRET_ID)
        return secret.get("_raw") or secret.get("github_pat") or secret.get("token") or ""
    except Exception:
        logger.exception("dogfood: failed to read github-token secret")
        return ""


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def circuit_open() -> bool:
    """
    True when the last CIRCUIT_WINDOW terminal runs had fewer than
    CIRCUIT_MIN_SUCCESSES successes. When open, new runs are suppressed
    until an operator resets by toggling DOGFOOD_ENABLED off and back on.
    """
    runs = overwatch_graph.list_dogfood_runs(limit=CIRCUIT_WINDOW * 3)
    terminal = [r for r in runs if r.get("status") in ("success", "failed", "timeout")]
    terminal = terminal[:CIRCUIT_WINDOW]
    if len(terminal) < CIRCUIT_WINDOW:
        return False
    successes = sum(1 for r in terminal if r.get("status") == "success")
    return successes < CIRCUIT_MIN_SUCCESSES


def _create_repo(name: str, token: str) -> bool:
    resp = httpx.post(
        f"{GITHUB_API}/user/repos",
        headers=_gh_headers(token),
        json={"name": name, "private": True, "auto_init": True},
        timeout=20,
    )
    if resp.status_code == 201:
        return True
    logger.warning("dogfood: repo create failed %s: %s", resp.status_code, resp.text[:200])
    return False


def _push_file(repo: str, path: str, content: str, token: str) -> None:
    httpx.put(
        f"{GITHUB_API}/repos/{GITHUB_USER}/{repo}/contents/{path}",
        headers=_gh_headers(token),
        json={
            "message": f"dogfood: add {path}",
            "content": base64.b64encode(content.encode()).decode(),
        },
        timeout=20,
    )


def _is_enabled() -> bool:
    """Check Neptune DogfoodConfig first, env var as fallback."""
    config = overwatch_graph.get_dogfood_config()
    neptune_flag = config.get("enabled")
    if neptune_flag is not None:
        return bool(neptune_flag)
    return os.environ.get("DOGFOOD_ENABLED", "").lower() in ("true", "1", "yes")


def _ensure_user_context(tenant_id: str, app: dict[str, Any],
                         project_id: str = "") -> None:
    """Write UserContext with product_vision so synthesis has context.

    Called AFTER project creation so the project_id can be included.
    Without this, _kick_off_synthesis silently no-ops and dogfood runs
    never get blueprints.
    """
    from datetime import datetime, timezone
    vision = app.get("desc") or app.get("name", "")
    name = app.get("name", "")
    now = datetime.now(timezone.utc).isoformat()
    try:
        result = overwatch_graph.query(
            "MERGE (u:UserContext {tenant_id: $tid}) "
            "SET u.product_vision = $vision, u.product_name = $name, "
            "u.target_users = $target, "
            "u.source = $source, u.updated_at = $now",
            {
                "tid": tenant_id,
                "vision": vision,
                "name": name,
                "target": "dogfood training simulator",
                "source": "dogfood",
                "now": now,
            },
        )
        logger.info(
            "dogfood: UserContext written for %s (vision=%s, project=%s, result=%s)",
            tenant_id[:12], vision[:40], project_id[:12], result,
        )
    except Exception as e:
        logger.warning("dogfood: UserContext write failed (continuing): %s", e)


def run_dogfood_cycle(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Kick off one dogfood deploy. Never blocks waiting for the deploy to
    complete — the sensor picks up the outcome on a later cycle.

    When an active DogfoodBatch exists the cycle runs even without
    explicit activation — the batch itself is the activation signal.
    """
    batch = overwatch_graph.get_active_batch()
    if not _is_enabled() and not batch:
        return {"skipped": True, "reason": "not enabled"}

    if not tenant_id:
        config = overwatch_graph.get_dogfood_config()
        tenant_id = config.get("tenant_id") or ""
    if not tenant_id:
        tenant_id = os.environ.get("DOGFOOD_TENANT_ID", "")
    if not tenant_id:
        return {"skipped": True, "reason": "no tenant_id"}

    if circuit_open():
        logger.warning("dogfood: circuit breaker OPEN — skipping run")
        return {"skipped": True, "reason": "circuit_open"}

    position = overwatch_graph.get_dogfood_cursor()
    app = pick_app(position)
    import time as _time
    repo_name = f"{app['name']}-{int(_time.time())}"

    if MODE != "production":
        # Local mode: record a pending run without hitting GitHub/Forgewing.
        run_id = overwatch_graph.record_dogfood_run(
            app_name=app["name"],
            fingerprint=app["fingerprint"],
            repo_name=repo_name,
            project_id=f"proj-local-{position}",
            tenant_id=tenant_id,
        )
        overwatch_graph.advance_dogfood_cursor()
        return {"status": "kicked_off", "run_id": run_id, "repo_name": repo_name,
                "app": app["name"], "mock": True}

    token = _gh_token()
    if not token:
        logger.warning("dogfood: no github token (key=%s, mode=%s)",
                        GITHUB_SECRET_ID, MODE)
        return {"status": "failed", "reason": f"no github token (key={GITHUB_SECRET_ID})"}

    if not _create_repo(repo_name, token):
        return {"status": "failed", "reason": "repo_create_failed"}

    for path, content in app["files"].items():
        _push_file(repo_name, path, content, token)

    repo_url = f"https://github.com/{GITHUB_USER}/{repo_name}"
    proj = forgewing_api.call_api(
        "POST", f"/projects/{tenant_id}",
        data={"name": repo_name, "repo_url": repo_url},
    )
    project_id = proj.get("project_id", "") if isinstance(proj, dict) else ""
    if not project_id or proj.get("error"):
        err = proj.get("error") if isinstance(proj, dict) else str(proj)[:200]
        logger.warning("dogfood: project_create_failed for %s: %s (response=%s)",
                        repo_name, err, str(proj)[:300])
        httpx.delete(f"{GITHUB_API}/repos/{GITHUB_USER}/{repo_name}",
                     headers=_gh_headers(token), timeout=10)
        return {"status": "failed", "reason": f"project_create_failed: {err}"}

    _ensure_user_context(tenant_id, app, project_id=project_id)

    deploy_resp = forgewing_api.call_api(
        "POST", "/api/v2/deploy",
        data={"tenant_id": tenant_id, "project_id": project_id,
              "trigger_source": "dogfood"},
    )
    exec_arn = (deploy_resp.get("execution_arn", "")
                if isinstance(deploy_resp, dict) else "")
    logger.info("dogfood: v2 deploy started, execution_arn=%s",
                exec_arn[-50:] if exec_arn else "<none>")

    run_id = overwatch_graph.record_dogfood_run(
        app_name=app["name"],
        fingerprint=app["fingerprint"],
        repo_name=repo_name,
        project_id=project_id,
        tenant_id=tenant_id,
    )
    overwatch_graph.advance_dogfood_cursor()
    logger.info("dogfood: kicked off %s (run_id=%s, project=%s)",
                repo_name, run_id, project_id)
    return {
        "status": "kicked_off",
        "run_id": run_id,
        "repo_name": repo_name,
        "project_id": project_id,
        "app": app["name"],
        "fingerprint": app["fingerprint"],
    }


registry.register(Capability(
    name="run_dogfood_cycle",
    function=run_dogfood_cycle,
    blast_radius=BLAST_DANGEROUS,
    description=(
        f"Automated dogfood deploy: creates a repo from the {len(CATALOGUE)}-app "
        "catalogue, triggers Forgewing deploy. Kickoff only — sensor handles "
        "outcome polling, reconciler cleans up. Gated: DOGFOOD_ENABLED=true required."
    ),
    requires_approval=True,
))
