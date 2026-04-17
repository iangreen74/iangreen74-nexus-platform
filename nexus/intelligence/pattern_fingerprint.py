"""Fine-grained pattern fingerprinting.

Each PR in a blueprint produces its own fingerprint, keyed by the
combination of what it builds + what it exposes + stack context +
resolved infrastructure. Fingerprints enable Sonnet bypass when
the exact PR shape has been proven before.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_pr_fingerprint(
    pr_spec: dict[str, Any],
    technical_stack: dict[str, Any] | None = None,
    infra_fragment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fingerprint dict for a blueprint PR.

    pr_spec: dict with keys builds, exposes, depends_on, etc.
    technical_stack: dict (language, framework, database)
    infra_fragment: CFN resources dict + env vars
    """
    components = {
        "builds": sorted(pr_spec.get("builds", [])),
        "exposes_keys": sorted((pr_spec.get("exposes") or {}).keys()),
        "stack": _canonicalize_stack(technical_stack or {}),
        "infra_resources": _canonicalize_infra(infra_fragment or {}),
    }
    payload = json.dumps(components, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
    slug = _make_slug(components)
    return {"fingerprint": digest, "slug": slug, "components": components}


def _canonicalize_stack(stack: dict[str, Any]) -> dict[str, str]:
    return {
        "language": (stack.get("language") or "").lower().strip(),
        "framework": (stack.get("framework") or "").lower().strip(),
        "database": (stack.get("database") or "").lower().strip(),
        "backend_target": (stack.get("backend_target") or "").lower().strip(),
        "frontend_target": (stack.get("frontend_target") or "").lower().strip(),
    }


def _canonicalize_infra(fragment: dict[str, Any]) -> list[str]:
    resources = fragment.get("Resources", {}) or {}
    return sorted({r.get("Type", "") for r in resources.values() if r})


def _make_slug(components: dict[str, Any]) -> str:
    stack = components["stack"]
    lang = stack.get("language", "?")
    fw = stack.get("framework", "?")
    first = (components["builds"] or ["?"])[0][:20]
    return f"{lang}-{fw}-{first.replace(' ', '-').lower()}"
