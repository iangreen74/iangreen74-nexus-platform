"""ALB access-log line parser + filter helpers used by ``alb_logs.py``.

Format reference:
https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-access-logs.html
"""
from __future__ import annotations

import shlex


# Field names per the ALB access-log spec (HTTPS / HTTP listeners; some
# fields are populated only for specific listener types).
FIELDS = (
    "type", "timestamp", "elb", "client_ip_port", "target_ip_port",
    "request_processing_time", "target_processing_time",
    "response_processing_time", "elb_status_code", "target_status_code",
    "received_bytes", "sent_bytes", "request", "user_agent",
    "ssl_cipher", "ssl_protocol", "target_group_arn", "trace_id",
    "domain_name", "chosen_cert_arn", "matched_rule_priority",
    "request_creation_time", "actions_executed", "redirect_url",
    "error_reason", "target_port_list", "target_status_code_list",
    "classification", "classification_reason",
)


def parse_log_line(line: str) -> dict | None:
    """Parse one ALB log line. Returns None for empty/invalid lines.

    ``shlex.split`` handles ALB's quoted-with-spaces fields correctly
    (e.g. the ``request`` field is ``"GET https://... HTTP/1.1"``).
    """
    line = line.strip()
    if not line:
        return None
    try:
        parts = shlex.split(line)
    except ValueError:
        return None
    return {name: parts[i] if i < len(parts) else None
            for i, name in enumerate(FIELDS)}


def matches(record: dict, flt: dict) -> bool:
    """Substring/prefix filter over a parsed log record. Empty matches all."""
    if not flt:
        return True
    sp = flt.get("status_prefix")
    if sp and not str(record.get("elb_status_code") or "").startswith(str(sp)):
        return False
    hc = flt.get("host_contains")
    if hc and hc not in (record.get("domain_name") or ""):
        return False
    pc = flt.get("path_contains")
    if pc and pc not in (record.get("request") or ""):
        return False
    tgc = flt.get("target_group_contains")
    if tgc and tgc not in (record.get("target_group_arn") or ""):
        return False
    return True
