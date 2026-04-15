"""
Finding formatting + deduplication.

Findings from multiple sources (cloudwatch errors, synthetic failures,
triage decisions, Neptune orphans) flow through this module so the
operator-facing report:

  - Dedups consecutive identical findings into "seen 3h ago, still open"
  - Groups each report cycle into NEW / ONGOING / RESOLVED
  - Leads every line with a one-line FIX, then ACTION, then optional PROMPT

Tracking state lives in a module-level registry keyed by a stable
fingerprint. A Neptune mirror can be layered later — for now the daemon
rebuilds the registry on startup, which is fine for consecutive-cycle
deduplication (the only grouping the report actually needs).
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_STOPWORDS = {"the", "a", "an", "of", "for", "on", "in", "to", "and", "or",
              "is", "are", "at", "with", "from", "by", "be", "this", "that",
              "it", "not", "but"}


@dataclass
class Finding:
    """A single actionable finding. All fields except `summary` optional."""
    summary: str
    severity: str = "warning"  # info | warning | critical
    category: str = "generic"  # code_fix | data_fix | config | deploy | other
    file: str | None = None
    line: int | None = None
    function: str | None = None
    action: str = ""
    prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable hash — same failure mode hashes the same across reports."""
        tokens = _keywords(self.summary)
        parts = [self.category, self.severity, self.file or "", str(self.line or "")]
        parts.extend(tokens[:6])
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _keywords(text: str) -> list[str]:
    """Normalized content tokens — strips numbers and stopwords."""
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", (text or "").lower())
    return [w for w in words if w not in _STOPWORDS][:12]


# --- Tracking registry ------------------------------------------------------

_lock = threading.Lock()
_registry: dict[str, dict[str, Any]] = {}
_last_cycle: set[str] = set()


def reset() -> None:
    global _registry, _last_cycle
    with _lock:
        _registry = {}
        _last_cycle = set()


def track_and_classify(findings: list[Finding]) -> dict[str, list[dict[str, Any]]]:
    """
    Classify findings into NEW / ONGOING / RESOLVED based on comparison
    with the previous cycle. Updates the registry as a side effect.
    Returns a dict with three lists; each entry has shape:
        {"finding": Finding, "first_seen": iso_ts, "cycles": int,
         "since": human_age, "resolved_at": iso_ts?}
    """
    global _last_cycle
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    current_fp: dict[str, Finding] = {f.fingerprint(): f for f in findings}
    new_entries: list[dict[str, Any]] = []
    ongoing_entries: list[dict[str, Any]] = []
    resolved_entries: list[dict[str, Any]] = []

    with _lock:
        for fp, finding in current_fp.items():
            entry = _registry.get(fp)
            if entry is None:
                _registry[fp] = {
                    "fingerprint": fp,
                    "first_seen_ts": now_ts,
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                    "cycles": 1,
                    "finding": finding,
                    "resolved_at": None,
                }
                new_entries.append(_snapshot(_registry[fp]))
            else:
                if entry.get("resolved_at"):
                    entry["resolved_at"] = None
                entry["last_seen"] = now_iso
                entry["cycles"] = entry.get("cycles", 0) + 1
                entry["finding"] = finding
                ongoing_entries.append(_snapshot(entry))

        for fp in _last_cycle - current_fp.keys():
            entry = _registry.get(fp)
            if entry is None or entry.get("resolved_at"):
                continue
            entry["resolved_at"] = now_iso
            resolved_entries.append(_snapshot(entry))

        _last_cycle = set(current_fp.keys())

    return {"new": new_entries, "ongoing": ongoing_entries, "resolved": resolved_entries}


def _snapshot(entry: dict[str, Any]) -> dict[str, Any]:
    now_ts = time.time()
    age = int(now_ts - entry.get("first_seen_ts", now_ts))
    return {
        "fingerprint": entry["fingerprint"],
        "finding": entry["finding"],
        "first_seen": entry.get("first_seen"),
        "last_seen": entry.get("last_seen"),
        "cycles": entry.get("cycles", 1),
        "since": _humanize_age(age),
        "resolved_at": entry.get("resolved_at"),
    }


def _humanize_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


# --- Formatting -------------------------------------------------------------


def format_finding(f: Finding) -> str:
    """One finding as FIX/ACTION/PROMPT block."""
    head = f"FIX: {f.summary}"
    if f.file:
        loc = f.file + (f":{f.line}" if f.line else "")
        fn = f" {f.function}()" if f.function else ""
        head = f"FIX: {loc}{fn} — {f.summary}"
    lines = [head]
    if f.action:
        lines.append(f"ACTION: {f.action}")
    if f.prompt:
        lines.append(f'PROMPT: "{f.prompt}"')
    return "\n".join(lines)


def format_report(grouped: dict[str, list[dict[str, Any]]]) -> str:
    """Render grouped findings into the operator-facing text block."""
    sections: list[str] = []

    if grouped.get("new"):
        sections.append("### NEW")
        for item in grouped["new"]:
            sections.append(format_finding(item["finding"]))
            sections.append("")

    if grouped.get("ongoing"):
        sections.append("### ONGOING")
        for item in grouped["ongoing"]:
            f = item["finding"]
            header = (f"- {f.summary} "
                      f"— first seen {item['since']}, {item['cycles']} report"
                      f"{'s' if item['cycles'] != 1 else ''}, still open")
            sections.append(header)
        sections.append("")

    if grouped.get("resolved"):
        sections.append("### RESOLVED")
        for item in grouped["resolved"]:
            f = item["finding"]
            sections.append(f"- {f.summary} — cleared "
                             f"(was open {item['since']})")
        sections.append("")

    if not sections:
        return "_No findings this cycle._"
    return "\n".join(sections).rstrip()


# --- Convenience builders ---------------------------------------------------


def from_cloudwatch_entry(entry: dict[str, Any]) -> Finding:
    """Convert a cloudwatch log entry into a Finding."""
    summary = entry.get("summary") or entry.get("message", "")[:160]
    exc = entry.get("exc") or ""
    file = entry.get("file")
    line = entry.get("line")
    function = entry.get("function") or ""
    short_summary = exc or summary
    action = ""
    prompt = ""
    if file:
        action = (f"Add type/shape guard in {function}() before the failing "
                  f"operation; skip when inputs are malformed.")
        prompt = (f"Fix the error in {file}"
                   + (f":{line}" if line else "")
                   + (f" {function}" if function else "")
                   + f" — {exc}. Add defensive guards and a test.")
    return Finding(
        summary=short_summary[:200],
        severity="critical" if "CRITICAL" in (entry.get("message") or "") else "warning",
        category="code_fix" if file else "other",
        file=file,
        line=line,
        function=function,
        action=action,
        prompt=prompt,
        metadata={"source": entry.get("source"),
                  "timestamp": entry.get("timestamp")},
    )


def from_synthetic_failure(result: dict[str, Any]) -> Finding:
    """Convert a failing synthetic journey into a Finding."""
    name = result.get("name", "synthetic")
    err = result.get("error", "")
    return Finding(
        summary=f"Synthetic '{name}' failed: {err}"[:200],
        severity="warning",
        category="config",
        action=f"Investigate {name} root cause; re-run once fixed.",
        prompt=f"Diagnose and fix the '{name}' synthetic failure: {err}",
    )
