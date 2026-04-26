# Nexus Routes Refactor Plan

**Status:** Design approved 2026-04-19. Execution target: Sprint 13.
**Owner:** Ian Green (CEO) + Claude (CTO/Lead Engineer).
**Scope:** Split `nexus/dashboard/routes.py` (2859 lines, 111 routes, 6 helpers)
into appropriately-scoped submodules matching the 200-line CI invariant.

## Why this exists

On April 19, 2026, the nexus deploy.yml hardening commit added a file-size
invariant enforcing a 200-line limit on non-exempt Python files (aligning with
the long-standing aria-platform convention). Running it against the nexus
codebase revealed 29 files over 200 lines, with `routes.py` at 2859 lines
being by far the worst offender. The invariant was softened to a warning to
allow CI to function while a proper refactor is planned.

Mass-exempting 29 files would make the invariant cosmetic. Doing the refactor
improvisationally under fatigue would risk breaking the operator console.
This plan specifies a deliberate, reviewable, incremental split that preserves
behavior and test coverage at every step.

## Target structure

```
nexus/dashboard/
    __init__.py
    routes.py               # Aggregator only: imports from submodules,
                            # exports `router`. <= 50 lines.
    routes/
        __init__.py         # Empty or package docstring only
        _helpers.py         # Shared formatters and Bedrock helpers
        platform.py         # /status, /daemon, /ci, /capabilities,
                            #   /actions, /graph/stats, /discovery
        tenants.py          # /tenants, /tenants/{tid}/*, /tenant-dive,
                            #   /tenant-report, /tenant-actions, /validate/tenants
        dogfood.py          # /dogfood/* (all 10 routes), /training-readiness
        learning.py         # /learning-overview, /learning-report,
                            #   /intelligence-score, /confidence-trajectory,
                            #   /trigger-finetuning, /cicd-metrics
        cicd.py             # /ci-gate*, /ci-decision, /ci/s3, /deploy-verify,
                            #   /deploy-drift, /deploy-patterns,
                            #   /deploy-decision, /deploy-outcome,
                            #   /synthetic-tests*
        patterns.py         # /patterns, /patterns/*, /findings/classify,
                            #   /triage/event, /heal-chains,
                            #   /signatures/bootstrap, /incident-signatures/bootstrap,
                            #   /incidents/close-stale
        diagnostics.py      # /diagnose/*, /diagnosis-history, /investigate,
                            #   /investigate/deep, /diagnostic-report,
                            #   /download-report, /investigations,
                            #   /feature-health
        forge.py            # /forge/* (all routes)
        research.py         # /research, /research/*, /proactive-scan,
                            #   /proactive-suggestions*
        release.py          # /release-checklist, /release-checklist/mark,
                            #   /pr-reality-check
        ops.py              # /ops/chat, /support/escalate, /support/escalations,
                            #   /code-audit*
        infrastructure.py   # /sre, /sre/incidents, /locks, /preemptive,
                            #   /runners, /runners/check, /bedrock-metrics,
                            #   /cost-summary, /onboarding-status,
                            #   /platform-healer, /neptune-integrity/scan,
                            #   /engineering-insights
        admin_debug.py      # /admin/advance-deploy, /debug/* (4 routes)
        onboarding.py       # (anticipated — currently empty)
```

Each submodule defines its own `router = APIRouter()` and the aggregator
imports + includes all of them. FastAPI supports this cleanly via
`router.include_router(submodule.router)` or equivalent.

## Route-to-submodule mapping (all 111 routes)

### platform.py (~8 routes)
- GET  /status
- GET  /daemon
- GET  /ci
- GET  /capabilities
- GET  /actions
- POST /actions/{action_id}/approve
- GET  /graph/stats
- GET  /discovery

### tenants.py (~8 routes)
- GET  /tenants
- GET  /tenants/{tenant_id}
- GET  /tenants/{tenant_id}/detail
- GET  /tenants/{tenant_id}/audit
- GET  /tenant-dive/{tenant_id}
- GET  /tenant-report/{tenant_id}
- GET  /tenant-actions
- GET  /validate/tenants

### dogfood.py (~11 routes)
- POST /dogfood/run-batch
- GET  /dogfood/batch-status
- POST /dogfood/cancel-batch
- GET  /dogfood/schedule
- POST /dogfood/schedule
- POST /dogfood/batch
- POST /dogfood/batch/cancel
- GET  /dogfood/watch
- GET  /dogfood/config
- POST /dogfood/config
- GET  /training-readiness

### learning.py (~6 routes)
- GET  /learning-overview
- GET  /confidence-trajectory
- POST /trigger-finetuning
- GET  /cicd-metrics
- GET  /intelligence-score
- GET  /learning-report

### cicd.py (~13 routes)
- POST /ci-gate
- POST /ci-gate-override
- GET  /ci-gate-override
- DELETE /ci-gate-override
- GET  /ci-decision
- GET  /ci/s3
- POST /deploy-verify
- GET  /deploy-drift
- GET  /deploy-patterns
- POST /deploy-decision
- POST /deploy-outcome
- POST /synthetic-tests (and /run, GET, /remediate)

### patterns.py (~10 routes)
- GET  /patterns
- GET  /patterns/candidates
- POST /patterns/capture-resolution
- POST /patterns/candidates/{name}/approve
- POST /patterns/candidates/{name}/reject
- POST /patterns/reload
- POST /findings/classify
- GET  /triage/event
- GET  /heal-chains
- POST /signatures/bootstrap
- POST /incident-signatures/bootstrap
- POST /incidents/close-stale

### diagnostics.py (~10 routes)
- POST /diagnose/goal
- POST /diagnose/tenant/{tenant_id}
- GET  /diagnose/status/{job_id}
- GET  /diagnosis-history
- POST /diagnose/{feature_id}
- POST /investigate
- POST /investigate/deep
- GET  /investigations
- GET  /diagnostic-report
- GET  /download-report
- GET  /feature-health

### forge.py (~6 routes)
- GET  /forge/prs
- GET  /forge/templates
- POST /forge/deploy/{service}
- GET  /forge/deploy/{service}
- POST /forge/propose-fix/{pattern_name}
- POST /forge/fix-agent/propose

### research.py (~8 routes)
- POST /research
- GET  /research
- GET  /research/{project_id}
- POST /research/{project_id}/run
- DELETE /research/{project_id}
- POST /proactive-scan
- GET  /proactive-suggestions
- GET  /proactive-suggestions/{tenant_id}

### release.py (~3 routes)
- GET  /release-checklist
- POST /release-checklist/mark/{item_name}
- GET  /pr-reality-check

### ops.py (~5 routes)
- POST /ops/chat
- POST /support/escalate
- GET  /support/escalations
- POST /code-audit
- GET  /code-audit
- GET  /code-audit/text

### infrastructure.py (~13 routes)
- GET  /sre
- GET  /sre/incidents
- GET  /locks
- GET  /preemptive
- GET  /runners
- POST /runners/check
- GET  /bedrock-metrics
- GET  /cost-summary
- GET  /onboarding-status
- GET  /platform-healer
- POST /neptune-integrity/scan
- GET  /engineering-insights

### admin_debug.py (~5 routes)
- POST /admin/advance-deploy/{tenant_id}
- GET  /debug/deploy-cycle/health
- POST /debug/resolve-stuck-runs
- GET  /debug/neptune-write-probe
- POST /debug/neptune-write-probe/cleanup

## Helper migration

| Helper | Line | Current Use | New Location |
|---|---|---|---|
| `_tenant_summary` | 50 | /status and /tenants | `_helpers.py` |
| `_format_report` | ~1043 | Tenant report formatting | `_helpers.py` |
| `_format_tenant_report` | ~1586 | Tenant report formatting | `_helpers.py` |
| `_build_ops_system_prompt` | ~2354 | /ops/chat Bedrock prompt | `ops.py` (private) |
| `_invoke_bedrock` | ~2379 | /ops/chat Bedrock invocation | `ops.py` (private) |
| `_resolve_auto_heal_capability` | ~2419 | /patterns and /triage | `_helpers.py` |

## Commit sequence

**Commit 1** — Create `nexus/dashboard/routes/` package with `__init__.py`,
`_helpers.py` containing the 4 shared helpers. Keep `routes.py` monolithic;
replace inline helper definitions with imports from `_helpers`. ~60 min.

**Commit 2** — Extract `platform.py` (8 routes). ~45 min.

**Commit 3** — Extract `tenants.py` (8 routes). ~45 min.

**Commit 4** — Extract `dogfood.py` (11 routes). ~45 min.

**Commit 5** — Extract `learning.py` (6 routes). ~30 min.

**Commit 6** — Extract `cicd.py` (13 routes). ~45 min.

**Commit 7** — Extract `patterns.py` (10 routes), `diagnostics.py` (10 routes).
Can be one commit because they're small individually. ~60 min.

**Commit 8** — Extract `forge.py` (6 routes), `research.py` (8 routes),
`release.py` (3 routes). ~60 min.

**Commit 9** — Extract `ops.py` (5 routes), `infrastructure.py` (13 routes),
`admin_debug.py` (5 routes). ~60 min.

**Commit 10** — Final cleanup: `routes.py` becomes <= 50 lines (aggregator
only). Remove file from any exemption list. Re-enable `exit 1` in deploy.yml
(reverting 595395b). ~30 min.

**Total estimated time:** 6-8 hours across 2-3 Sprint 13 days.

## Test coverage strategy

Each extraction commit must:

1. Confirm all tests referencing `nexus.dashboard.routes.<handler>` still pass
   (update import paths if necessary).
2. Run the full test suite locally before commit.
3. Run the full test suite on CI.
4. Smoke-check the dashboard at vaultscalerlabs.com after deploy. (Predecessor platform.vaultscaler.com RETIRED 2026-04-25.)

## Acceptance criteria

1. `routes.py` is <= 50 lines (aggregator only)
2. All 13 submodules + `_helpers.py` are each <= 200 lines
3. File-size invariant re-enabled to `exit 1`
4. Exempt list contains <= 5 files with written rationale
5. All 823+ tests pass
6. Dashboard behavior unchanged in production

## Risks and mitigations

**Risk:** FastAPI route conflicts during extraction.
**Mitigation:** Each commit removes routes from `routes.py` in the same
commit that adds them to the submodule. Never duplicate.

**Risk:** Import cycle between submodules.
**Mitigation:** Submodules import only from `_helpers.py` and from outside
`dashboard/`. No cross-submodule imports.

**Risk:** Tests fail after extraction due to patched import paths.
**Mitigation:** Each commit searches for `dashboard.routes.<handler>` in
`tests/` and updates in the same commit.

**Risk:** Production breakage mid-refactor.
**Mitigation:** Each commit is independently deployable. Circuit breaker
in deploy.yml provides auto-rollback.

## Out of scope

- Refactoring the 28 other non-exempt files over 200 lines (separate effort).
- Splitting `overwatch_graph.py` (exempt; ~1000 lines; canonical API).
- Introducing new routes or changing contracts (behavior-preserving only).

## Reference

- Violation audit: NEXUS Platform CI, April 19, 2026.
- Softening commit: 595395b.
- Target execution: Sprint 13 (April 21-27, 2026 tentative).
