"""
Incident Learning Loop — extract signatures from resolved incidents.

Every incident Overwatch resolves becomes a reusable detection signature:
- detection_key: structured match criteria (event_type, contains, field values)
- fix_template: suggested remediation capability/action
- confidence: grows with each successful reuse (+0.1 per match, cap 0.95)

Signatures are stored as IncidentSignature events and scanned against
active incidents so a past resolution can be suggested automatically.

Bootstrap: 5 canonical signatures seeded from today's real incidents.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph

logger = logging.getLogger(__name__)

MAX_CONFIDENCE = 0.95
CONFIDENCE_INCREMENT = 0.10


@dataclass
class IncidentSignature:
    signature_id: str
    name: str
    detection_key: dict[str, Any]  # {event_type, contains, field_equals}
    fix_template: dict[str, Any]   # {capability, kwargs, description}
    confidence: float = 0.5
    match_count: int = 0
    created_at: str = ""
    last_matched_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__,
                "detection_key": json.dumps(self.detection_key),
                "fix_template": json.dumps(self.fix_template)}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "IncidentSignature":
        out = dict(d)
        for k in ("detection_key", "fix_template"):
            if isinstance(out.get(k), str):
                try:
                    out[k] = json.loads(out[k])
                except (ValueError, TypeError):
                    out[k] = {}
        return IncidentSignature(
            signature_id=out.get("signature_id", ""),
            name=out.get("name", ""),
            detection_key=out.get("detection_key") or {},
            fix_template=out.get("fix_template") or {},
            confidence=float(out.get("confidence", 0.5)),
            match_count=int(out.get("match_count", 0)),
            created_at=out.get("created_at", ""),
            last_matched_at=out.get("last_matched_at", ""),
        )


def _sig_id(name: str, detection_key: dict) -> str:
    raw = f"{name}:{json.dumps(detection_key, sort_keys=True)}"
    return "sig_" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def learn_from_incident(name: str, detection_key: dict[str, Any],
                        fix_template: dict[str, Any]) -> IncidentSignature:
    """Create or merge a signature. Duplicates by (name, detection_key) merge."""
    sid = _sig_id(name, detection_key)
    existing = _load_signature(sid)
    if existing:
        existing.confidence = min(MAX_CONFIDENCE, existing.confidence + CONFIDENCE_INCREMENT)
        _store_signature(existing)
        return existing
    sig = IncidentSignature(
        signature_id=sid, name=name,
        detection_key=detection_key, fix_template=fix_template,
        created_at=_now(),
    )
    _store_signature(sig)
    return sig


def _load_signature(sid: str) -> IncidentSignature | None:
    for ev in overwatch_graph.get_recent_events(limit=500):
        if ev.get("event_type") != "incident_signature":
            continue
        details = ev.get("details") or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (ValueError, TypeError):
                continue
        if details.get("signature_id") == sid:
            return IncidentSignature.from_dict(details)
    return None


def _store_signature(sig: IncidentSignature) -> None:
    try:
        overwatch_graph.record_event(
            event_type="incident_signature",
            service=sig.signature_id,
            severity="info",
            details=sig.to_dict(),
        )
    except Exception:
        logger.debug("failed to store signature %s", sig.signature_id, exc_info=True)


def all_signatures() -> list[IncidentSignature]:
    """Return all known signatures, newest first, deduped by signature_id."""
    seen: dict[str, IncidentSignature] = {}
    for ev in overwatch_graph.get_recent_events(limit=500):
        if ev.get("event_type") != "incident_signature":
            continue
        details = ev.get("details") or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (ValueError, TypeError):
                continue
        sid = details.get("signature_id")
        if sid and sid not in seen:
            seen[sid] = IncidentSignature.from_dict(details)
    return list(seen.values())


def _matches(sig: IncidentSignature, incident: dict[str, Any]) -> bool:
    dk = sig.detection_key or {}
    want_type = dk.get("event_type")
    if want_type and incident.get("event_type") != want_type:
        return False
    contains = dk.get("contains") or []
    blob = json.dumps(incident, default=str).lower()
    for needle in contains:
        if str(needle).lower() not in blob:
            return False
    for field_name, expected in (dk.get("field_equals") or {}).items():
        if str(incident.get(field_name)) != str(expected):
            return False
    return True


def scan_all_signatures(incident: dict[str, Any]) -> list[dict[str, Any]]:
    """Scan every signature against an incident. Returns matches sorted by confidence."""
    if not incident:
        return []
    matches: list[dict[str, Any]] = []
    for sig in all_signatures():
        try:
            if _matches(sig, incident):
                sig.match_count += 1
                sig.last_matched_at = _now()
                _store_signature(sig)
                matches.append({
                    "signature_id": sig.signature_id,
                    "name": sig.name,
                    "confidence": sig.confidence,
                    "fix_template": sig.fix_template,
                })
        except Exception:
            logger.debug("scan failed for sig %s", sig.signature_id, exc_info=True)
    matches.sort(key=lambda m: m["confidence"], reverse=True)
    return matches


def bootstrap_signatures() -> list[IncidentSignature]:
    """Seed 5 canonical signatures from today's real incidents."""
    seeds = [
        ("bedrock_model_access_denied",
         {"event_type": "bedrock_error",
          "contains": ["AccessDenied", "Marketplace"]},
         {"capability": "create_foundation_model_agreement",
          "kwargs": {"secret_id": "forgescaler/api"},
          "description": "Bedrock marketplace agreement must be created for the model"}),
        ("forgewing_401_missing_api_key",
         {"event_type": "api_call_failed",
          "contains": ["Invalid API key", "401"]},
         {"capability": "attach_forgewing_api_key",
          "kwargs": {},
          "description": "call_api helper must attach X-API-Key header"}),
        ("deploy_stuck_not_started",
         {"event_type": "tenant_health",
          "field_equals": {"deploy_stage": "not_started"}},
         {"capability": "noop",
          "kwargs": {},
          "description": "not_started is PENDING, not stuck — no action needed"}),
        ("runner_disk_full",
         {"event_type": "runner_health",
          "contains": ["disk", "pct"]},
         {"capability": "restart_service",
          "kwargs": {"service": "docker-system-prune"},
          "description": "Prune Docker + /tmp via SSM when disk >80%"}),
        ("synthetic_project_separation",
         {"event_type": "synthetic_test",
          "contains": ["Identical brief", "separation broken"]},
         {"capability": "escalate_to_operator",
          "kwargs": {},
          "description": "Backend brief endpoint ignores project_id — requires code fix"}),
    ]
    return [learn_from_incident(name, dk, tpl) for name, dk, tpl in seeds]


def format_for_report() -> str:
    """Format signatures summary for the diagnostic report."""
    sigs = all_signatures()
    if not sigs:
        return "INCIDENT SIGNATURES: none (run bootstrap_signatures())"
    lines = [f"INCIDENT SIGNATURES: {len(sigs)} learned"]
    for s in sorted(sigs, key=lambda s: s.confidence, reverse=True)[:10]:
        lines.append(
            f"  {s.name} (conf={s.confidence:.2f}, matches={s.match_count})"
        )
    return "\n".join(lines)
