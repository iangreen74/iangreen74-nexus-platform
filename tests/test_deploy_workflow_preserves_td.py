"""Regression test: deploy workflow must preserve TD revisions.

B5-prov (DATABASE_URL) and B6 (FORGEWING_EVAL_CORPUS_BUCKET) were
wired into aria-console task definitions manually. If the CI deploy
workflow starts passing --task-definition to update-service, those
manually-wired env vars get wiped out on next deploy.

This test parses the deploy workflow and asserts it uses
--force-new-deployment without --task-definition.
"""
import os
import re
import pytest


WORKFLOW_GLOB_PATTERNS = [".github/workflows/deploy.yml"]


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_workflow():
    root = _repo_root()
    for pattern in WORKFLOW_GLOB_PATTERNS:
        path = os.path.join(root, pattern)
        if os.path.isfile(path):
            with open(path) as f:
                return f.read()
    return None


def test_deploy_uses_force_new_deployment():
    content = _read_workflow()
    if content is None:
        pytest.skip("no deploy.yml found")
    assert "force-new-deployment" in content, (
        "deploy workflow must use --force-new-deployment; otherwise the "
        "most recent task definition revision isn't picked up on push"
    )


def test_deploy_does_not_pass_task_definition_flag():
    """If CI passes --task-definition to update-service, manually-wired
    TD revisions (B5-prov DATABASE_URL, B6 FORGEWING_EVAL_CORPUS_BUCKET)
    get wiped on every deploy.

    update-service without --task-definition uses the service's latest TD.
    """
    content = _read_workflow()
    if content is None:
        pytest.skip("no deploy.yml found")

    blocks = re.split(r"\n\s*\n", content)
    offenders = []
    for block in blocks:
        if "update-service" in block and "--task-definition" in block:
            offenders.append(block.strip()[:200])

    assert not offenders, (
        "update-service is called with --task-definition in deploy.yml. "
        "This will wipe manually-wired env vars on every deploy. "
        "Use --force-new-deployment without --task-definition. "
        f"Offending block(s): {offenders}"
    )
