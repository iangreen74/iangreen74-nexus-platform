"""
Pattern Learner — the self-programming engine.

When a human resolves an incident that Overwatch couldn't auto-heal,
this module captures the resolution and generates a candidate pattern.
Candidate patterns sit in a holding area until they've been validated
enough times to graduate to full known patterns.

The loop:
  1. Incident escalated → human resolves it
  2. Resolution captured: what action, what root cause, should it auto-heal?
  3. Candidate pattern generated from the incident signature + resolution
  4. Next time the same signature fires → candidate proposes the heal
  5. Human approves → success count increments
  6. After N approvals → candidate graduates to permanent pattern
  7. System now handles this incident autonomously forever

This is the antifragile thesis made concrete: every incident that
requires human intervention eventually becomes one that doesn't.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.config import BLAST_SAFE

logger = logging.getLogger("nexus.reasoning.pattern_learner")

GRADUATION_THRESHOLD = 3
LEARNED_PATTERNS_FILE = "nexus/reasoning/learned_patterns.json"


@dataclass
class CandidatePattern:
    name: str
    signature: str
    match_source: str
    match_action: str
    heal_capability: str
    heal_kwargs_template: dict[str, str] = field(default_factory=dict)
    diagnosis: str = ""
    resolution: str = ""
    blast_radius: str = BLAST_SAFE
    confidence: float = 0.5
    success_count: int = 0
    failure_count: int = 0
    created_at: str = ""
    last_matched_at: str = ""
    graduated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "signature": self.signature,
            "match_source": self.match_source,
            "match_action": self.match_action,
            "heal_capability": self.heal_capability,
            "heal_kwargs_template": self.heal_kwargs_template,
            "diagnosis": self.diagnosis,
            "resolution": self.resolution,
            "blast_radius": self.blast_radius,
            "confidence": self.confidence,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "created_at": self.created_at,
            "last_matched_at": self.last_matched_at,
            "graduated": self.graduated,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "CandidatePattern":
        return CandidatePattern(**{k: v for k, v in d.items() if k in CandidatePattern.__dataclass_fields__})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signature_hash(source: str, action: str) -> str:
    raw = f"{source}:{action}"
    return "learned_" + hashlib.sha1(raw.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# In-memory candidate store
# ---------------------------------------------------------------------------
_candidates: dict[str, CandidatePattern] = {}


def _load_candidates() -> None:
    global _candidates
    try:
        rows = overwatch_graph.get_candidate_patterns()
        for row in rows:
            cp = CandidatePattern.from_dict(row)
            _candidates[cp.name] = cp
        if _candidates:
            logger.info("Loaded %d candidate patterns from graph", len(_candidates))
    except Exception:
        logger.debug("No candidate patterns loaded", exc_info=True)


def _save_candidate(cp: CandidatePattern) -> None:
    try:
        overwatch_graph.record_candidate_pattern(cp.to_dict())
    except Exception:
        logger.debug("Failed to persist candidate %s", cp.name, exc_info=True)


def get_candidates() -> list[CandidatePattern]:
    return list(_candidates.values())


def get_candidate(name: str) -> CandidatePattern | None:
    return _candidates.get(name)


# ---------------------------------------------------------------------------
# Resolution capture
# ---------------------------------------------------------------------------

def capture_resolution(
    incident_source: str,
    incident_action: str,
    heal_capability: str,
    root_cause: str,
    resolution_text: str,
    should_auto_heal: bool = False,
    blast_radius: str = BLAST_SAFE,
    heal_kwargs_template: dict[str, str] | None = None,
) -> CandidatePattern:
    """Capture a human resolution and generate a candidate pattern."""
    name = _signature_hash(incident_source, incident_action)

    existing = _candidates.get(name)
    if existing:
        existing.diagnosis = root_cause
        existing.resolution = resolution_text
        existing.heal_capability = heal_capability
        if should_auto_heal:
            existing.confidence = min(existing.confidence + 0.1, 0.95)
        _save_candidate(existing)
        return existing

    cp = CandidatePattern(
        name=name,
        signature=f"{incident_source}:{incident_action}",
        match_source=incident_source,
        match_action=incident_action,
        heal_capability=heal_capability,
        heal_kwargs_template=heal_kwargs_template or {},
        diagnosis=root_cause,
        resolution=resolution_text,
        blast_radius=blast_radius,
        confidence=0.5 if should_auto_heal else 0.3,
        created_at=_now_iso(),
    )
    _candidates[name] = cp
    _save_candidate(cp)

    overwatch_graph.record_human_decision(
        decision_type="resolution_capture",
        context=f"source={incident_source} action={incident_action}",
        action_taken=f"resolve with {heal_capability}",
        outcome="candidate_created",
        automatable=should_auto_heal,
    )

    logger.info("Created candidate pattern: %s (capability=%s)", name, heal_capability)
    return cp


# ---------------------------------------------------------------------------
# Candidate matching
# ---------------------------------------------------------------------------

def find_matching_candidate(source: str, action: str) -> CandidatePattern | None:
    """Check if any candidate pattern matches this incident signature."""
    for cp in _candidates.values():
        if cp.graduated:
            continue
        if cp.confidence < 0.5:
            continue
        source_matches = (
            cp.match_source == source
            or (cp.match_source.endswith(":*") and source.startswith(cp.match_source[:-1]))
        )
        action_matches = cp.match_action == action
        if source_matches and action_matches:
            cp.last_matched_at = _now_iso()
            return cp
    return None


# ---------------------------------------------------------------------------
# Approval / rejection
# ---------------------------------------------------------------------------

def approve_candidate(name: str) -> CandidatePattern | None:
    cp = _candidates.get(name)
    if not cp:
        return None
    cp.success_count += 1
    cp.confidence = min(cp.confidence + 0.15, 0.95)
    _save_candidate(cp)
    if cp.success_count >= GRADUATION_THRESHOLD and not cp.graduated:
        graduate_candidate(name)
    return cp


def reject_candidate(name: str, reason: str = "") -> CandidatePattern | None:
    cp = _candidates.get(name)
    if not cp:
        return None
    cp.failure_count += 1
    cp.confidence = max(cp.confidence - 0.15, 0.1)
    _save_candidate(cp)
    return cp


# ---------------------------------------------------------------------------
# Graduation
# ---------------------------------------------------------------------------

def graduate_candidate(name: str) -> CandidatePattern | None:
    cp = _candidates.get(name)
    if not cp:
        return None
    cp.graduated = True
    cp.confidence = max(cp.confidence, 0.85)
    _save_candidate(cp)
    _append_to_learned_patterns(cp)
    try:
        overwatch_graph.record_event(
            "pattern_graduated",
            cp.name,
            {
                "capability": cp.heal_capability,
                "success_count": cp.success_count,
                "signature": cp.signature,
            },
            "info",
        )
    except Exception:
        pass
    logger.info("Pattern graduated: %s (capability=%s, %d successes)", name, cp.heal_capability, cp.success_count)
    return cp


def _append_to_learned_patterns(cp: CandidatePattern) -> None:
    try:
        import os
        patterns: list[dict[str, Any]] = []
        if os.path.exists(LEARNED_PATTERNS_FILE):
            with open(LEARNED_PATTERNS_FILE) as f:
                patterns = json.load(f)
        patterns.append(cp.to_dict())
        with open(LEARNED_PATTERNS_FILE, "w") as f:
            json.dump(patterns, f, indent=2)
    except Exception:
        logger.exception("Failed to write learned patterns file")


def load_graduated_patterns() -> list[CandidatePattern]:
    try:
        import os
        if not os.path.exists(LEARNED_PATTERNS_FILE):
            return []
        with open(LEARNED_PATTERNS_FILE) as f:
            return [CandidatePattern.from_dict(p) for p in json.load(f)]
    except Exception:
        return []


# Load on import
try:
    _load_candidates()
except Exception:
    pass
