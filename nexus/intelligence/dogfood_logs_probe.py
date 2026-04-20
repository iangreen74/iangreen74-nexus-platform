"""CloudWatch Logs probe for dogfood diagnostic reports.

Fetches log events from the forgewing-v2-stage log group around a
given timestamp. All functions catch exceptions and never raise.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("nexus.intelligence.dogfood_logs_probe")

LOG_GROUP = "/ecs/forgewing-v2-stage"

_ERROR_PATTERN = re.compile(
    r"(ERROR|Exception|Traceback|CRITICAL|FATAL|panic|RuntimeError)",
    re.IGNORECASE,
)

_logs_client = None


def _logs():
    """Lazy boto3 CloudWatch Logs client."""
    global _logs_client
    if _logs_client is None:
        import boto3
        _logs_client = boto3.client("logs", region_name="us-east-1")
    return _logs_client


def _parse_iso(iso_str: str) -> int:
    """Convert ISO timestamp to epoch milliseconds."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def fetch_logs_around_time(
    iso_timestamp: str,
    window_minutes: int = 10,
    limit: int = 100,
    filter_pattern: str = "",
) -> list[dict[str, Any]]:
    """Fetch CloudWatch log events around a given ISO timestamp.

    Returns a list of dicts with timestamp, message, and logStreamName.
    """
    try:
        center_ms = _parse_iso(iso_timestamp)
        delta_ms = window_minutes * 60 * 1000
        start_ms = center_ms - delta_ms
        end_ms = center_ms + delta_ms

        params: dict[str, Any] = {
            "logGroupName": LOG_GROUP,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": min(limit, 10000),
            "interleaved": True,
        }
        if filter_pattern:
            params["filterPattern"] = filter_pattern

        resp = _logs().filter_log_events(**params)
        return [
            {
                "timestamp": ev.get("timestamp"),
                "message": ev.get("message", ""),
                "logStreamName": ev.get("logStreamName", ""),
            }
            for ev in resp.get("events", [])
        ]
    except Exception:
        logger.exception("fetch_logs_around_time failed for %s", iso_timestamp)
        return []


def find_error_lines(events: list[dict[str, Any]]) -> list[str]:
    """Filter log events for ERROR/Exception/Traceback markers.

    Returns a list of message strings that match error patterns.
    """
    errors: list[str] = []
    for ev in events:
        msg = ev.get("message", "")
        if _ERROR_PATTERN.search(msg):
            errors.append(msg.strip())
    return errors
