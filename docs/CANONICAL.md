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

## Architectural debt — Phase 2 work items

- **Two-actor approval token gate.** Search key: **`KmsHmacApprovalToken_SeparationOfDuties`**.
  Key resource policy author (Phase 0/1) intended separation of duties — reasoner role *signs* (`kms:GenerateMac`), mutation role *verifies* (`kms:VerifyMac`). Phase 1 runtime in `nexus/overwatch_v2/auth/approval_tokens.py` is single-actor: both `_sign` and `_verify_sig` run as whichever role the executing task assumed (in production, the reasoner role). Phase 1.5.3 (PR #43, 2026-04-26) **aligned the key resource policy with the single-actor runtime as a tactical fix** by adding `kms:VerifyMac` to `ReasonerRoleSignVerify` (formerly `ReasonerRoleSign`). This collapses the intended security separation; documented here so Phase 2 can restore it.
  - **Phase 2 sub-agent architecture must restore:**
    1. Operator UI propose/execute split — distinct user actions for the two phases of any mutation.
    2. STS `AssumeRole` from reasoner into `overwatch-v2-mutation-role` before `verify_mac` in `_verify_sig` (and remove `kms:VerifyMac` from `ReasonerRoleSignVerify` as the final step).
    3. `sts:AssumeRole` grant on the reasoner role's identity policy targeting the mutation role's ARN.
    4. Tests for STS failure modes — assume denied, session expired, mutation role missing `kms:VerifyMac`.
    5. Audit log entries that distinguish the proposer (reasoner) from the executor (mutation role) on every mutation, so post-incident forensics can answer "who proposed this and who executed it?" rather than "the agent did it."
  - **Diagnostic-quality sub-finding (Sprint 14 Day 3+):** Phase 1.5.2's identity-policy fix used `Condition: StringEquals: kms:ResourceAliases: ...` — but `kms:ResourceAliases` is a *multi-valued* context key per AWS IAM docs, requiring `ForAnyValue:StringEquals` (or `ForAllValues:StringEquals`). Plain `StringEquals` against a multi-valued key has undefined behavior; AWS IAM Simulator confirmed zero matched statements even with the context populated. The Phase 0/1 (dead) `kms:Verify` grant used the same wrong operator — never noticed because the action was wrong anyway. When Phase 2 retires the IAM-condition path along with restoring two-actor separation, this also goes away. Until then: do not trust IAM identity-policy grants on KMS resources gated by `kms:ResourceAliases` without verifying with `ForAnyValue:StringEquals`.

- **Uncodified external state.** Search key: **`UncodifiedExternalState`**.
  GitHub App `overwatch-v2-reasoner` permissions are managed in github.com UI, not in any CFN template or Terraform module in this repo. Phase 1.5.3's four-probe verification (2026-04-26) revealed the gap when probe 1's outbound `comment_on_pr` returned GitHub `403 "Resource not accessible by integration"` — the App was installed read-only on `iangreen74/aria-platform` because Phase 1 was the first mutation tool and the install permissions were never bumped. Substrate-truth save number 8 of the day, and the first save in a different class than the prior seven (which were all internal substrate). Same shape for OAuth callback URLs, customer Cognito pool app-client configs, third-party tokens, vendor IAM trust relationships, DNS at the registrar level — anything that lives outside our IaC and is managed in a vendor's UI.
  - **Phase 2 work item:** dedicated `EXTERNAL_STATE_INVENTORY.md` runbook tracking every piece of uncodified external state, where it lives, who can change it, and the operator-action procedure for each change (with screenshots / step-by-step where the UI is involved). Every PR that adds a new external integration must add an entry. The 8th substrate-truth save will always be in something we forgot to look at — documentation is how we shrink that surface.
  - **Phase 1.5.4 immediate follow-up (operator action, not code):** grant `Issues: Write` to the `overwatch-v2-reasoner` GitHub App installation on `iangreen74/aria-platform`. Re-run the four-probe task. Real comment URL lands on PR #71. Phase 1 declared "fully verified end-to-end" only after that.

## Locked principles

- **Operational Truth Substrate Architecture** (locked 2026-04-25). Authoritative spec at [`OPERATIONAL_TRUTH_SUBSTRATE.md`](OPERATIONAL_TRUTH_SUBSTRATE.md). Defines Phase 0 (substrate: Layer 1 raw sources, Layer 2 synthesis primitives, Layer 3 Operational Graph) → Phase 1+ (reports + actions) sequencing for all Overwatch v2 capability work. Supersedes prior report-first sequencing. Companion to V2 Spec Invariant C.
- **Operational Truth as Engineering Value** (locked 2026-04-25). Joins "abstract expressionism", "antifragile engineering", and "user is never lost" as a top-level principle. When Echo (or any system in this codebase) answers an operational question, the answer must be grounded in evidence with citations — never "I think", always "the data shows X, supported by [locator]". Methodology lesson L39.
- **Abstract expressionism in software:** The user never sees the mechanism.
- **Antifragile engineering:** Systems improve under stress.
- **The user is never lost:** Every screen state has a visible next action.
