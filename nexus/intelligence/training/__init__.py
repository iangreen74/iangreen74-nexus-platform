"""Anvil training harness: loads deployment corpus from Neptune, produces
trained fingerprint similarity + outcome prediction models.

Design source: SUBSTRATE.md (data layer), ANVIL_ARCHITECTURE.md (model tier).
Sprint 15 bake-off (per VISION.md) lands the first trained checkpoint.

Until then, BaselineHeuristicModel provides retrieval-based predictions
using the pattern library directly — no ML, just Neptune RAG.
"""
