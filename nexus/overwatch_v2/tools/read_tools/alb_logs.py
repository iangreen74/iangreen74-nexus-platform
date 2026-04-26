"""Phase 0b read_alb_logs — parse S3-stored ALB access logs.

Lists log objects within the time window, downloads + parses gzipped
files into structured records. Two known buckets:
  - aria-platform-alb-logs-418295677815 (legacy aria-platform-alb)
  - overwatch-v2-alb-logs-418295677815  (vaultscalerlabs.com)

ALB access log format reference:
https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-access-logs.html

Bounded by a 6h window and 1000 records (logs are very high-volume).
"""
from __future__ import annotations

import gzip
import io
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools._alb_log_parser import (
    matches as _matches,
    parse_log_line as _parse_log_line,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


TOOL_NAME = "read_alb_logs"
MAX_WINDOW_HOURS = 6
MAX_RECORDS = 1000

KNOWN_BUCKETS = {
    "aria-platform-alb-logs-418295677815",
    "overwatch-v2-alb-logs-418295677815",
}

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "bucket": {"type": "string", "description": f"One of: {sorted(KNOWN_BUCKETS)}"},
        "start_time": {"type": "string", "description": "ISO-8601; default: now - 1h."},
        "end_time": {"type": "string",
                     "description": f"ISO-8601; capped to start + {MAX_WINDOW_HOURS}h."},
        "filter": {
            "type": "object",
            "description": ("Optional substring filters: status_prefix (e.g. '5'), "
                            "host_contains, path_contains, target_group_contains."),
        },
        "max_records": {"type": "integer",
                        "description": f"Default 200, hard cap {MAX_RECORDS}."},
    },
    "required": ["bucket"],
}


def _parse(ts: str | None, default: datetime) -> datetime:
    if not ts:
        return default
    s = ts.rstrip("Z")
    if "+" not in s and "-" not in s[10:]:
        s = s + "+00:00"
    return datetime.fromisoformat(s)


def _list_keys(s3, bucket: str, start: datetime, end: datetime) -> list[str]:
    """List S3 keys whose ALB-log timestamp falls in [start, end]."""
    prefix_root = f"AWSLogs/418295677815/elasticloadbalancing/us-east-1/"
    keys: list[str] = []
    cursor = start
    seen_prefixes: set[str] = set()
    while cursor <= end:
        p = f"{prefix_root}{cursor.year:04d}/{cursor.month:02d}/{cursor.day:02d}/"
        if p not in seen_prefixes:
            seen_prefixes.add(p)
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=p):
                for obj in page.get("Contents") or []:
                    key = obj.get("Key", "")
                    keys.append(key)
        cursor += timedelta(hours=1)
    keys.sort()
    return keys


def handler(**params: Any) -> dict:
    bucket = params.get("bucket")
    if bucket not in KNOWN_BUCKETS:
        raise ToolUnknown(f"bucket {bucket!r} not in known set {sorted(KNOWN_BUCKETS)}")

    now = datetime.now(timezone.utc)
    start = _parse(params.get("start_time"), now - timedelta(hours=1))
    end = _parse(params.get("end_time"), now)
    capped = False
    max_end = start + timedelta(hours=MAX_WINDOW_HOURS)
    if end > max_end:
        end = max_end
        capped = True

    requested_max = int(params.get("max_records") or 200)
    if requested_max > MAX_RECORDS:
        raise ToolUnknown(
            f"max_records {requested_max} exceeds hard cap {MAX_RECORDS}"
        )
    requested_max = max(1, min(requested_max, MAX_RECORDS))

    flt = params.get("filter") or {}

    try:
        from nexus.aws_client import _client
        s3 = _client("s3")
        keys = _list_keys(s3, bucket, start, end)
        records: list[dict] = []
        for key in keys:
            if len(records) >= requested_max:
                break
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read()
            try:
                text = gzip.decompress(body).decode("utf-8", errors="replace")
            except OSError:
                text = body.decode("utf-8", errors="replace")
            for line in io.StringIO(text):
                rec = _parse_log_line(line)
                if not rec:
                    continue
                rec_ts = rec.get("timestamp")
                if rec_ts:
                    try:
                        when = datetime.fromisoformat(rec_ts.replace("Z", "+00:00"))
                        if when < start or when > end:
                            continue
                    except Exception:
                        pass
                if not _matches(rec, flt):
                    continue
                records.append(rec)
                if len(records) >= requested_max:
                    break
    except Exception as e:
        raise map_boto_error(e) from e

    return {
        "bucket": bucket,
        "records": records,
        "total_count": len(records),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_capped_to_6h": capped,
        "objects_scanned": len(keys),
        "filter_applied": flt or None,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name=TOOL_NAME,
        description=(
            "Read S3-stored ALB access logs. Window hard-capped to "
            f"{MAX_WINDOW_HOURS}h, records to {MAX_RECORDS}. "
            "Bucket must be one of the two managed access-log buckets."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))
