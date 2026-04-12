"""
CI Results Reader — consumes CI events from S3.

CI publishes results to s3://hyperlev-builds/ci-events/latest.json
after every run. Overwatch reads this for real-time CI awareness
instead of polling the GitHub API.

Deploy outcomes are published to s3://hyperlev-builds/deploy-events/latest.json
and consumed by deploy_patterns.py for pattern learning.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from nexus.config import MODE

logger = logging.getLogger(__name__)

_BUCKET = "hyperlev-builds"
_CI_KEY = "ci-events/latest.json"
_DEPLOY_KEY = "deploy-events/latest.json"
_TIMEOUT = 5  # seconds — don't hang the triage cycle


def _read_s3_json(key: str) -> dict[str, Any] | None:
    """Read a JSON object from S3. Returns None on any failure."""
    if MODE != "production":
        return None  # tests inject mock data directly
    try:
        from nexus.aws_client import _client

        s3 = _client("s3")
        obj = s3.get_object(Bucket=_BUCKET, Key=key)
        return json.loads(obj["Body"].read())
    except Exception as exc:
        logger.debug("S3 read %s/%s failed: %s", _BUCKET, key, exc)
        return None


def get_latest_ci_result() -> dict[str, Any] | None:
    """Read the latest CI result from S3."""
    return _read_s3_json(_CI_KEY)


def get_latest_deploy_outcome() -> dict[str, Any] | None:
    """Read the latest deploy outcome from S3."""
    return _read_s3_json(_DEPLOY_KEY)


def get_ci_health_summary() -> dict[str, Any]:
    """Summarize CI health for the diagnostic report.

    Returns a dict with status, test counts, commit info, and run URL.
    Falls back gracefully when S3 data is unavailable.
    """
    result = get_latest_ci_result()
    if not result:
        return {"source": "s3", "status": "unavailable"}

    total = result.get("total_tests", 0)
    passed = result.get("passed_tests", 0)
    failed_list = result.get("failed_tests", [])
    status = result.get("status", "unknown")

    return {
        "source": "s3",
        "status": status,
        "total_tests": total,
        "passed_tests": passed,
        "failed_tests": failed_list,
        "failed_count": len(failed_list) if isinstance(failed_list, list) else 0,
        "commit_sha": result.get("commit_sha", ""),
        "commit_message": result.get("commit_message", ""),
        "timestamp": result.get("timestamp", ""),
        "run_url": result.get("run_url", ""),
        "duration_seconds": result.get("duration_seconds"),
    }


def get_deploy_outcome_summary() -> dict[str, Any]:
    """Summarize latest deploy outcome for the diagnostic report."""
    result = get_latest_deploy_outcome()
    if not result:
        return {"source": "s3", "status": "unavailable"}

    return {
        "source": "s3",
        "status": result.get("status", "unknown"),
        "service": result.get("service", ""),
        "commit_sha": result.get("commit_sha", ""),
        "commit_message": result.get("commit_message", ""),
        "timestamp": result.get("timestamp", ""),
        "environment": result.get("environment", ""),
    }
