"""Bootstrap OperatorFeature definitions to Neptune.

PR-G1 — closes the artifact-vs-persistence gap surfaced during Phase 0e.4
production rendering on 2026-04-28. Phase 0e.4 (PR #57/#58) shipped the
``ontology_capture_loop`` OperatorFeature as a Python module-level
constant at ``nexus/operator_features/instances/ontology_capture_loop.py``.
Nothing previously called ``write_operator_feature`` against it, so the
operational graph contained no node for it; ``read_holograph`` returned
the engine's "not found" stub.

This script imports every module under
``nexus/operator_features/instances/`` exposing a ``FEATURE`` constant,
and calls ``write_operator_feature`` on each. Idempotent: the underlying
persistence path uses MERGE-by-(feature_id, tenant_id) semantics — re-
running with no definition change is a no-op; re-running after a change
updates the version_id.

Usage:
    # Local discovery (no Neptune mutation, validates imports):
    ARIA_GRAPH_BACKEND=local python scripts/bootstrap_operator_features.py --dry-run

    # Production (Neptune mutation, requires verbal Ian confirmation per
    # CANONICAL gating; run from a VPC-connected runtime):
    ARIA_GRAPH_BACKEND=neptune python scripts/bootstrap_operator_features.py

PR-G2 generalises this into a server.py startup hook so future
OperatorFeature instances bootstrap automatically. After PR-G2 ships,
this script remains useful for explicit re-bootstrap after definition
changes (idempotent).

Refs: docs/operator_features_bootstrap_runbook.md
"""
from __future__ import annotations

import argparse
import importlib
import logging
import pkgutil
import sys

from nexus.operator_features.persistence import write_operator_feature
from nexus.operator_features.schema import OperatorFeature

import nexus.operator_features.instances as _instances_pkg

logger = logging.getLogger("bootstrap_operator_features")


def discover_features() -> list[OperatorFeature]:
    """Import every module under instances/ and collect FEATURE constants.

    Convention: every instance module exposes a module-level
    ``FEATURE = OperatorFeature(...)``. Modules whose names start with
    ``_`` are skipped (private). Modules without a FEATURE constant or
    with a FEATURE that isn't an OperatorFeature instance are warned
    about and skipped — the loop continues so a typo in one module
    doesn't block bootstrapping the others.
    """
    features: list[OperatorFeature] = []
    for _finder, name, _ispkg in pkgutil.iter_modules(_instances_pkg.__path__):
        if name.startswith("_"):
            continue
        module = importlib.import_module(
            f"{_instances_pkg.__name__}.{name}"
        )
        if not hasattr(module, "FEATURE"):
            logger.warning(
                "Module %s has no FEATURE constant; skipping. "
                "Convention: every instance module exposes module-level FEATURE.",
                name,
            )
            continue
        feature = module.FEATURE
        if not isinstance(feature, OperatorFeature):
            logger.warning(
                "Module %s FEATURE is %s, not OperatorFeature; skipping.",
                name, type(feature).__name__,
            )
            continue
        features.append(feature)
    return features


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap OperatorFeatures to Neptune.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Discover only; do not call write_operator_feature.",
    )
    args = parser.parse_args(argv)

    features = discover_features()
    logger.info("Discovered %d OperatorFeature instance(s):", len(features))
    for f in features:
        logger.info(
            "  feature_id=%s name=%r signals=%d evidence_queries=%d",
            f.feature_id, f.name,
            len(f.health_signals), len(f.evidence_queries),
        )

    if args.dry_run:
        logger.info(
            "DRY RUN — no Neptune mutation. "
            "Re-run without --dry-run to bootstrap."
        )
        return 0

    if not features:
        logger.error("No features discovered; nothing to bootstrap.")
        return 1

    written = 0
    for f in features:
        try:
            node_id = write_operator_feature(f)
            logger.info(
                "wrote feature_id=%s to Neptune (node_id=%s)",
                f.feature_id, node_id,
            )
            written += 1
        except Exception as exc:  # noqa: BLE001 — log + continue
            logger.exception(
                "FAILED to write feature_id=%s: %s", f.feature_id, exc,
            )

    logger.info(
        "Bootstrap complete: %d/%d features written", written, len(features),
    )
    return 0 if written == len(features) else 2


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    sys.exit(main())
