# Reports Phase 2 — Substrate Inventory

**Authored:** 2026-04-26
**Status:** committed alongside the Phase 2 ship of `/api/reports`. See [`OVERWATCH_V2_REPORTS_ARCHITECTURE.md`](OVERWATCH_V2_REPORTS_ARCHITECTURE.md) for the 12-report catalog spec.

This doc explains **why only 3 of 12 reports shipped** in Phase 2 and exactly **what unblocks each deferred report**. The 9 deferred entries are first-class in the API (every `GET /api/reports` response surfaces them with structured `deferred_reasons`); this doc is the operator-facing companion.

---

## Substrate at the time of Phase 2 ship

15 read tools registered:

1. `read_aws_resource`
2. `read_cloudwatch_logs`
3. `read_github`
4. `query_pipeline_truth`
5. `query_engineering_ontology`
6. `read_overwatch_metrics`
7. `list_aws_resources`
8. `read_repo_file`         *(Phase 0a)*
9. `search_codebase`        *(Phase 0a)*
10. `read_git_diff`          *(Phase 0a)*
11. `list_repo_files`        *(Phase 0a)*
12. `read_customer_tenant_state` *(Phase 1)*
13. `read_customer_pipeline`     *(Phase 1)*
14. `read_customer_ontology`     *(Phase 1)*
15. `read_aria_conversations`    *(Phase 1)*

Auth: ALB front door (Cognito) — no FastAPI middleware in this codebase.

---

## Verdict per report

| # | Name | Tier | Verdict | Substrate gap (if deferred) |
|---|---|---|---|---|
| 1 | Fleet Health Overview | T1 | **FEASIBLE-NOW** *(current state only)* | 7-day trend defers to ontology-snapshot history |
| 2 | Critical Findings (24h) | T1 | DEFERRED | `requires_phase_0b_log_correlation`, `requires_mechanism_2_classifier_table` |
| 3 | Pipeline Activity (24h) | T1 | **FEASIBLE-NOW** *(raw status grouping)* | semantic failure-type grouping defers to classifier substrate |
| 4 | Tenant Operational Profile | T2 | **FEASIBLE-NOW** | — |
| 5 | Tenant Failure Diagnose | T2 | DEFERRED | `requires_phase_0b_log_correlation`, `requires_learned_pattern_library` |
| 6 | Tenant Conversation Trajectory | T2 | DEFERRED | `requires_classifier_proposals_schema` |
| 7 | Cross-Tenant Failure Patterns | T3 | DEFERRED | `requires_phase_0b_log_correlation`, `requires_mechanism_2_classifier_table` |
| 8 | Compounding Loop Health | T3 | DEFERRED | `requires_ontology_snapshot_history` |
| 9 | Goal Health (V1 parity) | T3 | DEFERRED | `requires_mechanism_2_classifier_table` *(synthesizes from Reports 2–3)* |
| 10 | Recommended Actions Queue | T4 | DEFERRED | `requires_learned_pattern_library`, `requires_mutation_tooling` |
| 11 | Pattern-Based Action Plans | T4 | DEFERRED | `requires_phase_0b_log_correlation`, `requires_learned_pattern_library`, `requires_mutation_tooling` |
| 12 | Capability Gap & Investment Suggestions | T4 | DEFERRED | `requires_echo_capability_gap_capture` |

**Tally:** 3 feasible-now, 9 deferred.

---

## Structured deferred-reason enums

Every deferred report carries one or more enum reasons (`nexus/reports/catalog.py`). Each maps to a concrete substrate gap:

| Enum | What's missing | Unblocks |
|---|---|---|
| `requires_phase_0b_log_correlation` | Cross-source log index (CloudTrail + ALB + CW correlated by request/deploy/tenant) | 2, 5 (Tier 2), 7, 11 |
| `requires_mechanism_2_classifier_table` | Deploy-event classifier output table (build error / deploy error / smoke fail / rollback) | 2, 7, 9 |
| `requires_learned_pattern_library` | Versioned library of known fix patterns | 5 (Tier 3), 10, 11 |
| `requires_classifier_proposals_schema` | Per-conversation classifier proposals + accept/reject/edit records | 6 |
| `requires_ontology_snapshot_history` | Hourly ontology snapshots so growth/trend can be computed | 1 (7-day trend), 8 |
| `requires_echo_capability_gap_capture` | Structured logging when Echo says "I don't have enough data" | 12 |
| `requires_mutation_tooling` | Approval-gated mutation tools + workflow state | 10, 11 |

---

## What ships in this PR

- `GET /api/reports` returns the 12-entry catalog. Each entry: `report_id`, `name`, `tier`, `audience`, `description`, `params_schema`, `feasible_now`, `deferred_reasons`, `required_tools`.
- `POST /api/reports/{report_id}/run` — feasible reports execute and return a structured envelope with sections; deferred reports return an empty-sections envelope plus the `deferred_reasons` enum list (no builder runs, no tools called).
- Right-panel UI lists all 12 reports; deferred entries render greyed with a tooltip showing the enum reasons.

## What's next (substrate, not reports)

Pick one substrate gap and close it; multiple reports unblock per substrate change:

- **Phase 0b log correlation** is the highest-leverage next move — unblocks Reports 2, 5 (Tier 2), 7, 11. Four reports for one substrate piece.
- Mechanism 2 classifier table next — unblocks 2, 7, 9. Three reports.
- LearnedPattern library — unblocks 5 (Tier 3), 10, 11.

Phase 2 stops here so the substrate question gets first-class attention, not a thin-veneer continuation.
