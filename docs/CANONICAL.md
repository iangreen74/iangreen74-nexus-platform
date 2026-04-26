# CANONICAL

This document records VaultScaler's locked decisions — the architectural and strategic choices that do not get relitigated in ordinary sprint work. New entries land here only after explicit approval. Once a document is listed here, treat its contents as load-bearing and propose changes via dedicated review, not in-line edits during feature work.

## Locked documents

| Document | Date | Status |
|---|---|---|
| [`OVERWATCH_V2_SPECIFICATION.md`](OVERWATCH_V2_SPECIFICATION.md) | 2026-04-24 | canonical |
| [`OVERWATCH_V2_REPORTS_ARCHITECTURE.md`](OVERWATCH_V2_REPORTS_ARCHITECTURE.md) | 2026-04-25 | canonical (Phase 2 detail of the substrate spec) |
| [`OPERATIONAL_TRUTH_SUBSTRATE.md`](OPERATIONAL_TRUTH_SUBSTRATE.md) | 2026-04-25 | canonical |

## Locked principles

- **Operational Truth Substrate Architecture** (locked 2026-04-25). Authoritative spec at [`OPERATIONAL_TRUTH_SUBSTRATE.md`](OPERATIONAL_TRUTH_SUBSTRATE.md). Defines Phase 0 (substrate: Layer 1 raw sources, Layer 2 synthesis primitives, Layer 3 Operational Graph) → Phase 1+ (reports + actions) sequencing for all Overwatch v2 capability work. Supersedes prior report-first sequencing. Companion to V2 Spec Invariant C.
- **Operational Truth as Engineering Value** (locked 2026-04-25). Joins "abstract expressionism", "antifragile engineering", and "user is never lost" as a top-level principle. When Echo (or any system in this codebase) answers an operational question, the answer must be grounded in evidence with citations — never "I think", always "the data shows X, supported by [locator]". Methodology lesson L39.
