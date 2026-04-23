"""Mechanism 3 — Socratic proactive prompts."""
from nexus.mechanism3.rules import SocraticPrompt, scan_tenant
from nexus.mechanism3.store import (
    mark_acknowledged, mark_surfaced, read_pending_prompts, save_prompts,
)

__all__ = [
    "SocraticPrompt", "scan_tenant",
    "save_prompts", "read_pending_prompts",
    "mark_surfaced", "mark_acknowledged",
]
