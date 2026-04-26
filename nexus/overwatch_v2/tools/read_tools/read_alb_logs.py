"""Phase 0b read_alb_logs: parse ALB access logs from S3.

Spec: docs/OPERATIONAL_TRUTH_SUBSTRATE.md L145. List day prefixes that
overlap the time window, gunzip + parse each ALB access log .gz,
return a uniform Phase 0b envelope.
"""
from __future__ import annotations

import gzip
import io
import re
import shlex
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


SOURCE, ACCOUNT_ID, REGION = "alb", "418295677815", "us-east-1"
DEFAULT_BUCKET = "overwatch-v2-alb-logs-418295677815"
MAX_WINDOW_HOURS, DEFAULT_WINDOW_MINUTES = 24, 60
MAX_EVENTS, DEFAULT_EVENTS = 1000, 200
MAX_OBJECT_BYTES = 5 * 1024 * 1024


PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "start_time": {"type": "string", "description": "ISO-8601. Default now-60min."},
        "end_time": {"type": "string", "description": "ISO-8601. Default now. Capped to start+24h."},
        "alb_name": {"type": "string", "description": "Substring matched against the lb-name in filename."},
        "min_status": {"type": "integer", "description": "Filter elb_status_code >= this."},
        "path_substring": {"type": "string", "description": "Filter requests whose path contains this."},
        "max_events": {"type": "integer", "description": f"Cap {MAX_EVENTS}; default {DEFAULT_EVENTS}."},
        "bucket": {"type": "string", "description": f"Override default ({DEFAULT_BUCKET})."},
    },
    "required": [],
}

# 30-field ALB access-log format (shlex posix=True handles quoted strings).
_ALB_FIELDS = (
    "type", "timestamp", "alb", "client_addr", "target_addr",
    "request_processing_time", "target_processing_time",
    "response_processing_time", "elb_status_code", "target_status_code",
    "received_bytes", "sent_bytes", "request", "user_agent",
    "ssl_cipher", "ssl_protocol", "target_group_arn", "trace_id",
    "domain_name", "chosen_cert_arn", "matched_rule_priority",
    "request_creation_time", "actions_executed", "redirect_url",
    "error_reason", "target_port_list", "target_status_code_list",
    "classification", "classification_reason", "conn_trace_id",
)
_FILENAME_TS_RE = re.compile(r"_(\d{8}T\d{4}Z)_")


def _client():
    from nexus.aws_client import _client as factory
    return factory("s3")


def _parse_iso(ts: str | None, default: datetime) -> datetime:
    if not ts:
        return default
    s = ts.rstrip("Z")
    if "+" not in s and "-" not in s[10:]:
        s = s + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise ToolUnknown(f"bad ISO-8601 timestamp {ts!r}: {e}") from e


def _bound_window(start: str | None, end: str | None
                  ) -> tuple[datetime, datetime, bool]:
    now = datetime.now(timezone.utc)
    e = _parse_iso(end, now)
    s = _parse_iso(start, e - timedelta(minutes=DEFAULT_WINDOW_MINUTES))
    capped = False
    if e - s > timedelta(hours=MAX_WINDOW_HOURS):
        e = s + timedelta(hours=MAX_WINDOW_HOURS); capped = True
    return s, e, capped


def _day_prefixes(start: datetime, end: datetime) -> list[str]:
    out: list[str] = []
    cur = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    while cur <= end:
        out.append(f"AWSLogs/{ACCOUNT_ID}/elasticloadbalancing/{REGION}/"
                   f"{cur.year:04d}/{cur.month:02d}/{cur.day:02d}/")
        cur += timedelta(days=1)
    return out


def _object_in_window(key: str, start: datetime, end: datetime) -> bool:
    """Filename embeds a 5-min-bucket end. Use it to skip irrelevant
    objects without fetching. Loose: accept overlapping buckets."""
    m = _FILENAME_TS_RE.search(key)
    if not m:
        return True
    try:
        ts = datetime.strptime(m.group(1), "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return ts >= (start - timedelta(minutes=10)) and ts <= (end + timedelta(minutes=10))


def _parse_line(line: str) -> dict[str, Any] | None:
    try:
        parts = shlex.split(line, posix=True)
    except ValueError:
        return None
    rec = {n: (parts[i] if i < len(parts) else None) for i, n in enumerate(_ALB_FIELDS)}
    sc = rec.get("elb_status_code")
    if sc and sc != "-":
        try: rec["elb_status_code"] = int(sc)
        except ValueError: pass
    return rec


def _iter_records(bucket: str, key: str) -> Iterator[dict[str, Any]]:
    s3 = _client()
    head = s3.head_object(Bucket=bucket, Key=key)
    if (head.get("ContentLength") or 0) > MAX_OBJECT_BYTES:
        return
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
        for raw in gz:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            rec = _parse_line(line)
            if rec:
                yield rec


def _matches(rec: dict[str, Any], alb: str, min_st: int | None, path_sub: str) -> bool:
    if alb and alb not in (rec.get("alb") or ""): return False
    if min_st is not None and (rec.get("elb_status_code") or 0) < min_st: return False
    if path_sub and path_sub not in (rec.get("request") or ""): return False
    return True


def _stream_records(bucket: str, s: datetime, e: datetime) -> Iterator[dict[str, Any]]:
    s3 = _client()
    for prefix in _day_prefixes(s, e):
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if not _object_in_window(obj["Key"], s, e):
                    continue
                yield from _iter_records(bucket, obj["Key"])


def handler(**params: Any) -> dict[str, Any]:
    s, e, window_capped = _bound_window(
        params.get("start_time"), params.get("end_time"),
    )
    bucket = params.get("bucket") or DEFAULT_BUCKET
    alb = params.get("alb_name") or ""
    min_st = params.get("min_status")
    path_sub = params.get("path_substring") or ""
    cap = max(1, min(int(params.get("max_events") or DEFAULT_EVENTS), MAX_EVENTS))
    events: list[dict[str, Any]] = []
    truncated = window_capped
    try:
        for rec in _stream_records(bucket, s, e):
            if not _matches(rec, alb, min_st, path_sub):
                continue
            events.append(rec)
            if len(events) >= cap:
                truncated = True
                break
    except Exception as ex:
        raise map_boto_error(ex) from ex
    return {
        "source": SOURCE,
        "time_range": {"start": s.isoformat(), "end": e.isoformat()},
        "count": len(events),
        "truncated": truncated,
        "events": events[:cap],
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_alb_logs",
        description=(
            "Phase 0b: parse ALB access logs from S3 within a time window. "
            "Filters: alb_name substring, min_status, path_substring. "
            "Default 60 min; cap 24h. Returns parsed records (type, "
            "timestamp, client_addr, request, elb_status_code, etc.)."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))
