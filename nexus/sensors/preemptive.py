"""
Preemptive Health Sensor.

Instead of waiting for failures, actively look for conditions that WILL
fail soon. Each check returns zero or more PreemptiveAlert dicts with:

    check_name        — short identifier
    severity          — info | warning | critical
    message           — human-readable summary
    time_until_failure — ISO timestamp or None if not predictable
    suggested_action  — concrete next step

Some checks are real (ECS task age, ACM cert expiry). Others are stubbed
honestly with `status="unknown_needs_wiring"` so the dashboard surfaces
the gap rather than faking confidence we don't have.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import aws_client
from nexus.config import (
    FORGEWING_CLUSTER,
    FORGEWING_SERVICES,
    KNOWN_SECRET_EXPIRIES,
    MODE,
    PREEMPTIVE_CERT_EXPIRY_DAYS,
    PREEMPTIVE_SECRET_EXPIRY_DAYS,
    PREEMPTIVE_TASK_AGE_DAYS,
)

logger = logging.getLogger("nexus.sensors.preemptive")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alert(
    check_name: str,
    severity: str,
    message: str,
    *,
    time_until_failure: str | None = None,
    suggested_action: str = "",
    status: str = "ok",
) -> dict[str, Any]:
    return {
        "check_name": check_name,
        "severity": severity,
        "message": message,
        "time_until_failure": time_until_failure,
        "suggested_action": suggested_action,
        "status": status,
    }


def check_ecs_task_age() -> list[dict[str, Any]]:
    """Flag any ECS task running longer than PREEMPTIVE_TASK_AGE_DAYS."""
    if MODE != "production":
        return []
    alerts: list[dict[str, Any]] = []
    threshold = timedelta(days=PREEMPTIVE_TASK_AGE_DAYS)
    try:
        ecs = aws_client._client("ecs")
        for service in FORGEWING_SERVICES:
            arns = ecs.list_tasks(cluster=FORGEWING_CLUSTER, serviceName=service).get("taskArns", [])
            if not arns:
                continue
            tasks = ecs.describe_tasks(cluster=FORGEWING_CLUSTER, tasks=arns).get("tasks", [])
            for task in tasks:
                started = task.get("startedAt")
                if not started:
                    continue
                age = _now() - started.replace(tzinfo=timezone.utc) if started.tzinfo is None else _now() - started
                if age > threshold:
                    alerts.append(
                        _alert(
                            "ecs_task_age",
                            severity="warning",
                            message=f"{service} task running for {age.days}d (>{PREEMPTIVE_TASK_AGE_DAYS}d threshold)",
                            suggested_action=f"Force a new deployment of {service} to refresh the task.",
                        )
                    )
    except Exception:
        logger.exception("check_ecs_task_age failed")
    return alerts


def check_certificate_expiry() -> list[dict[str, Any]]:
    """Alert when any ACM certificate is within PREEMPTIVE_CERT_EXPIRY_DAYS of expiry."""
    if MODE != "production":
        return []
    alerts: list[dict[str, Any]] = []
    threshold = timedelta(days=PREEMPTIVE_CERT_EXPIRY_DAYS)
    try:
        acm = aws_client._client("acm")
        page = acm.list_certificates(CertificateStatuses=["ISSUED"], MaxItems=200)
        for summary in page.get("CertificateSummaryList", []):
            arn = summary.get("CertificateArn")
            domain = summary.get("DomainName")
            if not arn:
                continue
            try:
                detail = acm.describe_certificate(CertificateArn=arn).get("Certificate", {})
            except Exception:
                continue
            not_after = detail.get("NotAfter")
            if not not_after:
                continue
            if not_after.tzinfo is None:
                not_after = not_after.replace(tzinfo=timezone.utc)
            remaining = not_after - _now()
            if remaining <= threshold:
                severity = "critical" if remaining <= timedelta(days=7) else "warning"
                alerts.append(
                    _alert(
                        "certificate_expiry",
                        severity=severity,
                        message=f"ACM cert for {domain} expires in {remaining.days}d",
                        time_until_failure=not_after.isoformat(),
                        suggested_action=f"Renew/reissue ACM certificate {arn}",
                    )
                )
    except Exception:
        logger.exception("check_certificate_expiry failed")
    return alerts


def check_known_secret_expiries() -> list[dict[str, Any]]:
    """
    Alert when any tracked secret is within PREEMPTIVE_SECRET_EXPIRY_DAYS
    of its known expiry. Secrets Manager doesn't store PAT expiry in
    metadata, so we track expected dates explicitly via config.
    """
    alerts: list[dict[str, Any]] = []
    if not KNOWN_SECRET_EXPIRIES:
        return alerts
    threshold = timedelta(days=PREEMPTIVE_SECRET_EXPIRY_DAYS)
    for name, iso_date in KNOWN_SECRET_EXPIRIES.items():
        try:
            expiry = datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        remaining = expiry - _now()
        if remaining <= threshold:
            severity = "critical" if remaining <= timedelta(days=3) else "warning"
            alerts.append(
                _alert(
                    "secret_expiry",
                    severity=severity,
                    message=f"Secret {name} expires in {remaining.days}d",
                    time_until_failure=expiry.isoformat(),
                    suggested_action=f"Rotate {name} in Secrets Manager and update KNOWN_SECRET_EXPIRIES.",
                )
            )
    return alerts


def check_github_token_freshness() -> list[dict[str, Any]]:
    """
    Per-tenant GitHub installation token freshness.

    GitHub App installation tokens expire at 60 minutes. We can't see
    when they were last refreshed without observing aria-platform's
    own behavior — that bridge needs to be built. Reported as
    `unknown_needs_wiring` so the dashboard shows the gap honestly.
    """
    return [
        _alert(
            "github_token_freshness",
            severity="info",
            message="No tracking yet — needs aria-platform telemetry hook",
            suggested_action="Wire aria-platform to write GitHubTokenRefresh nodes that Overwatch can read.",
            status="unknown_needs_wiring",
        )
    ]


def check_neptune_storage() -> list[dict[str, Any]]:
    """
    Neptune Analytics storage utilization.

    Neptune Analytics doesn't expose storage usage via the data plane —
    we'd need CloudWatch metrics in `AWS/Neptune Analytics`. Stubbed
    until that wiring lands.
    """
    return [
        _alert(
            "neptune_storage",
            severity="info",
            message="No usage telemetry — needs CloudWatch GraphSize metric wiring",
            suggested_action="Pull AWS/Neptune Analytics GraphSize via CloudWatch and compare to provisioned memory.",
            status="unknown_needs_wiring",
        )
    ]


def check_bedrock_throttling() -> list[dict[str, Any]]:
    """
    Bedrock invocation rate vs service quota.

    Requires Service Quotas + CloudWatch metric correlation. Not yet
    wired — reported honestly so the dashboard reflects the gap.
    """
    return [
        _alert(
            "bedrock_throttling",
            severity="info",
            message="No quota telemetry — needs ServiceQuotas + CloudWatch wiring",
            suggested_action="Pull bedrock:InvokeModel CloudWatch metrics and compare to ServiceQuotas limit.",
            status="unknown_needs_wiring",
        )
    ]


def run_preemptive_checks() -> list[dict[str, Any]]:
    """
    Run every preemptive check and return the combined alert list.
    Each check is independent — one failing must not block the others.
    """
    all_alerts: list[dict[str, Any]] = []
    for check_fn in (
        check_ecs_task_age,
        check_certificate_expiry,
        check_known_secret_expiries,
        check_github_token_freshness,
        check_neptune_storage,
        check_bedrock_throttling,
    ):
        try:
            all_alerts.extend(check_fn())
        except Exception:
            logger.exception("preemptive check %s crashed", check_fn.__name__)
    return all_alerts
