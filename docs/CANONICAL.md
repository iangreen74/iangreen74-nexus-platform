# CANONICAL — Locked Decisions

This document records VaultScaler's locked decisions — the architectural,
operational, and identity choices that do not get relitigated in ordinary
sprint work. Once an entry is listed here, treat its contents as
load-bearing and propose changes via dedicated review, not in-line edits
during feature work.

Last updated: 2026-04-25 (Sprint 14 Day 1)

---

## Operator Console (Overwatch)

- **Domain:** `vaultscalerlabs.com` (apex)
- **Migrated:** 2026-04-25 (PR #24)
- **Predecessor:** `platform.vaultscaler.com` — RETIRED 2026-04-25
- **Auth:** AWS Cognito with MFA (TOTP) at the ALB front door
- **Cognito Operator Pool:** `overwatch-operators` (separate from the customer pool)
- **Sign-out:** clean redirect to Cognito sign-in form, full session invalidation (`/oauth2/sign-out`, PR #19)
- **ALB:** dedicated `overwatch-v2-alb` (independent of `aria-platform-alb`)
- **ALB Access Logs:** enabled day-1, written to `s3://overwatch-v2-alb-logs-418295677815/`

## Operator Authentication & Identity

- **Cognito Operator Pool ID:** `us-east-1_SsglIc4iM`
- **Customer Pool (separate):** `us-east-1_3dzaO4Dzl`
- **GitHub App for Overwatch:** `overwatch-v2-reasoner` (replaces deleted PAT)
- **GitHub App credentials secret:** `overwatch-v2/github-app`

## V2 Substrate

- **V2 Neptune Analytics graph:** `g-279kpnulx0`
- **V2 Neptune private endpoint:** auto-provisioned via `CreatePrivateGraphEndpoint` API (PR #21)
- **Note:** legacy zone `Z0336367224PC62D12VOO` is service-owned, not customer-managed

## Architecture & Roadmap References

| Document | Date | Status |
|---|---|---|
| [`OVERWATCH_V2_SPECIFICATION.md`](OVERWATCH_V2_SPECIFICATION.md) | 2026-04-24 | canonical |
| [`OVERWATCH_V2_REPORTS_ARCHITECTURE.md`](OVERWATCH_V2_REPORTS_ARCHITECTURE.md) | 2026-04-25 | canonical (Phase 2 detail of the substrate spec) |
| [`OPERATIONAL_TRUTH_SUBSTRATE.md`](OPERATIONAL_TRUTH_SUBSTRATE.md) | 2026-04-25 | canonical |
| [`OVERWATCH_AUTONOMY_ROADMAP.md`](OVERWATCH_AUTONOMY_ROADMAP.md) | rolling | canonical roadmap |
| [`OVERWATCH_V2_EXECUTION_PLAN.md`](OVERWATCH_V2_EXECUTION_PLAN.md) | rolling | execution plan |

## Locked principles

- **Operational Truth Substrate Architecture** (locked 2026-04-25). Authoritative spec at [`OPERATIONAL_TRUTH_SUBSTRATE.md`](OPERATIONAL_TRUTH_SUBSTRATE.md). Defines Phase 0 (substrate: Layer 1 raw sources, Layer 2 synthesis primitives, Layer 3 Operational Graph) → Phase 1+ (reports + actions) sequencing for all Overwatch v2 capability work. Supersedes prior report-first sequencing. Companion to V2 Spec Invariant C.
- **Operational Truth as Engineering Value** (locked 2026-04-25). Joins "abstract expressionism", "antifragile engineering", and "user is never lost" as a top-level principle. When Echo (or any system in this codebase) answers an operational question, the answer must be grounded in evidence with citations — never "I think", always "the data shows X, supported by [locator]". Methodology lesson L39.
- **Abstract expressionism in software:** The user never sees the mechanism.
- **Antifragile engineering:** Systems improve under stress.
- **The user is never lost:** Every screen state has a visible next action.
