# Overwatch — Autonomous Platform Engineering for Forgewing

## Classification: Verified — April 11, 2026

Every count, capability name, and endpoint in this document was audited against the deployed codebase on the date above. If it says "shipped," it runs in production.

---

## What Overwatch Is

Overwatch is a fully separate control plane that monitors, diagnoses, heals, engineers fixes for, and reports on the Forgewing platform autonomously. It connects to Forgewing through Neptune Analytics, AWS APIs, HTTP health checks, and the GitHub API — never by importing Forgewing code. The goal: one human (Ian) operates a global product with zero engineering hires, because Overwatch handles reliability, diagnosis, and recovery.

```
Ian (CEO) → manages Overwatch (the only system he touches)
Overwatch → manages Forgewing (zero human intervention needed)
Forgewing → serves customers (zero downtime, continuous experience)
```

## Architecture

| Property | Value |
|----------|-------|
| Repo | `iangreen74/iangreen74-nexus-platform` |
| ECS Service | `aria-console` (cluster `aria-platform`) |
| ECR Image | `nexus-platform:latest` |
| Task Definition | `aria-console` |
| Operator Console | `vaultscalerlabs.com` (Cognito MFA at ALB; sign-out via `/oauth2/sign-out`). Migrated 2026-04-25; predecessor `platform.vaultscaler.com` RETIRED. |
| Operator ALB | `overwatch-v2-alb` (dedicated, independent of `aria-platform-alb`). Access logs day-1 to `s3://overwatch-v2-alb-logs-418295677815/`. |
| Neptune Graph | `g-1xwjj34141` (shared with Forgewing, `Overwatch*` label namespace) |
| AWS Account | 418295677815, us-east-1 |
| aria-platform repo | `iangreen74/aria-platform` |
| Overwatch PR label | `overwatch-fix` |
| Python files | 46 |
| Lines of code | ~9,400 |
| Tests | 213 |
| Capabilities | 24 |
| Heal chains | 11 |
| Triage patterns | 23 (hand-coded) + graduated learned patterns |
| API endpoints | 39 |

---

## 5 Autonomy Levels (All Shipped)

### Level 1 — Observe & Alert
Read-only monitoring + Telegram/dashboard alerts. Always automatic, exempt from rate limits.

Capabilities: `send_telegram_alert`, `send_escalation`, `get_failing_workflows`, `get_service_logs`, `diagnose_daemon_timeout`, `check_daemon_code_version`, `check_deploy_readiness`, `validate_tenant_onboarding`, `validate_repo_indexing`, `check_pipeline_health`, `verify_write_access`, `diagnose_tenant_deploy`, `get_project_lifecycle`, `check_all_tenants_lifecycle`

### Level 2 — Heal & Restore
Reversible write operations with guardrails. Auto-fires when confidence >= 0.8 and blast_radius == safe. Rate limited: 10 actions/hour global, 30-minute cooldown per action+target.

Capabilities: `restart_daemon`, `restart_service`, `refresh_tenant_token`, `retrigger_workflow`, `retrigger_ingestion`, `retry_tenant_deploy`, `diagnose_and_fix_deploy`

### Level 3 — Performance Drift Detection
Proactive detection of degrading trends before they become incidents. Auto-fires heal chains for: daemon cycle drift, PR generation slowdown, tenant velocity drops, context health decline.

### Level 4 — Self-Programming
Pattern graduation system. When a human resolves an escalation, Overwatch captures the resolution as a candidate pattern. After 3 approvals, the candidate graduates to a permanent known pattern and Overwatch handles it autonomously forever. Graduated patterns persist in `learned_patterns.json` and auto-load on startup.

### Level 5 — Auto-Discovery
Overwatch probes Forgewing's API endpoints and discovers new capabilities without code changes. When a new endpoint appears (e.g., `/smoke-test`, `/deploy-preview`), Overwatch starts monitoring it automatically and records the discovery in the graph.

---

## Capabilities (24)

| # | Name | Blast Radius | Description |
|---|------|-------------|-------------|
| 1 | `send_telegram_alert` | Safe | Send alert to Overwatch Telegram chat |
| 2 | `send_escalation` | Safe | Formatted escalation with diagnosis + suggested action |
| 3 | `get_failing_workflows` | Safe | List GitHub Actions failures with step detail + run URLs |
| 4 | `retrigger_workflow` | Moderate | Re-run a failed GitHub Actions workflow |
| 5 | `restart_daemon` | Moderate | Force new deployment of aria-daemon + verify |
| 6 | `diagnose_daemon_timeout` | Safe | Analyze daemon logs to identify slow hooks |
| 7 | `check_daemon_code_version` | Safe | Compare running image digest vs ECR latest |
| 8 | `check_deploy_readiness` | Safe | Identify deployment blockers before retry |
| 9 | `diagnose_and_fix_deploy` | Moderate | Readiness check → fix auto-fixable blockers → retry |
| 10 | `restart_service` | Moderate | Force new deployment for any ECS service |
| 11 | `get_service_logs` | Safe | Fetch recent CloudWatch log events for ECS service |
| 12 | `refresh_tenant_token` | Safe | Mint fresh GitHub App token from installation_id |
| 13 | `validate_tenant_onboarding` | Safe | Full onboarding checklist: tenant, token, write access, files, tasks |
| 14 | `verify_write_access` | Safe | Test write access via create-ref/delete-ref |
| 15 | `retrigger_ingestion` | Moderate | Re-ingest tenant repo via Forgewing API |
| 16 | `validate_repo_indexing` | Safe | Check RepoFile count in Neptune |
| 17 | `check_pipeline_health` | Safe | Analyze task/PR pipeline for blockers |
| 18 | `diagnose_tenant_deploy` | Safe | Diagnose stuck/failed tenant deployment (CF, CodeBuild, progress) |
| 19 | `retry_tenant_deploy` | Moderate | Retry stuck deployment via Forgewing deploy API |
| 20 | `get_project_lifecycle` | Safe | Check active project, archived count, restart status |
| 21 | `check_all_tenants_lifecycle` | Safe | Lifecycle check for all tenants — flags abandonment, stale restarts |
| 22 | `investigate_stuck_tasks` | Safe | Mapped action → check_pipeline_health |
| 23 | `diagnose_and_fix_deploy` (auto-fix) | Moderate | Auto-fixable blockers: missing_slr, no_dockerfile, build_failed |
| 24 | `diagnose_and_fix_deploy` (escalate) | Moderate | User-action-required: no_aws_role, stuck_stack |

**Safety gates:** Confidence >= 0.8 (safe) / >= 0.9 (moderate). Dangerous blast radius always escalates. 30-minute cooldown per action+target. 10 actions/hour global budget.

---

## Heal Chains (11)

Multi-step recovery sequences with verification gates between steps.

| # | Chain | Trigger | Steps | Max Attempts |
|---|-------|---------|-------|-------------|
| 1 | `daemon_stale` | Daemon stale/no recent cycle | restart_daemon → diagnose_daemon_timeout → check_daemon_code_version | 3 |
| 2 | `ci_failing` | CI failures / low green rate | retrigger_workflow (wait 2.5min) | 3 |
| 3 | `empty_tenant_token` | Token empty | refresh_tenant_token → validate_tenant_onboarding | — |
| 4 | `missing_repo_files` | 0 repo files after ingestion | retrigger_ingestion → validate_repo_indexing | — |
| 5 | `tenant_no_prs_after_tasks` | Tasks exist, 0 PRs after 2h | validate_tenant_onboarding → check_pipeline_health | — |
| 6 | `tenant_capability_blocked` | Capability report: blocked | validate_tenant_onboarding → refresh_tenant_token | — |
| 7 | `daemon_cycle_drift` | Anomalous daemon cycle duration (L3) | diagnose_daemon_timeout → check_daemon_code_version | — |
| 8 | `pr_generation_slowdown` | PR generation time degrading (L3) | check_pipeline_health → validate_tenant_onboarding | — |
| 9 | `tenant_velocity_drop` | Task velocity zero, was active (L3) | validate_tenant_onboarding → check_pipeline_health | — |
| 10 | `context_health_decline` | Context health <4 active sources (L3) | validate_tenant_onboarding | — |
| 11 | `tenant_deploy_stuck` | Deploy stuck | check_deploy_readiness → diagnose_and_fix_deploy → validate_tenant_onboarding | — |

---

## Triage Patterns (23)

| # | Pattern | Trigger | Auto-Heal? | Confidence |
|---|---------|---------|-----------|------------|
| 1 | `github_permission_denied` | "permission" + "denied" or "403" | No — escalate | 0.95 |
| 2 | `bedrock_json_parse` | "cannot parse" + bedrock/json | Yes — retry with fence stripping | 0.9 |
| 3 | `step_functions_access_denied` | AccessDeniedException + states: | No — escalate | 0.85 |
| 4 | `daemon_stale` | daemon_stale event | Yes — restart daemon | 0.9 |
| 5 | `ci_failing` | CI failing / workflow failed | Yes — retrigger CI | 0.85 |
| 6 | `tenant_no_prs_after_tasks` | Tasks >0, PRs =0, >2h | Yes — validate onboarding | 0.9 |
| 7 | `missing_repo_files` | 0 repo files, ingestion complete | Yes — retrigger ingestion | 0.9 |
| 8 | `empty_tenant_token` | Token empty | Yes — refresh token | 0.95 |
| 9 | `write_access_denied` | Write access = false | No — escalate | 0.95 |
| 10 | `tenant_capability_blocked` | Capability report: blocked | Yes — validate onboarding | 0.9 |
| 11 | `tenant_capability_degraded` | Capability report: degraded | Yes — check pipeline | 0.8 |
| 12 | `daemon_timeout_recurring` | >=3 timeouts in 1h | No — escalate | 0.85 |
| 13 | `daemon_cycle_drift` | Anomalous cycle duration (L3) | Yes — diagnose | 0.85 |
| 14 | `pr_generation_slowdown` | PR gen time degrading (L3) | Yes — investigate | 0.75 |
| 15 | `tenant_velocity_drop` | Velocity zero, was active (L3) | Yes — validate | 0.8 |
| 16 | `context_health_decline` | Context health <4 active (L3) | Yes — validate | 0.7 |
| 17 | `tenant_deploy_stuck` | Deploy stuck | No — diagnose + fix chain | 0.85 |
| 18 | `tenant_deploy_precondition_failed` | Deploy readiness: user action needed | No — escalate | 0.9 |
| 19 | `tenant_project_archived` | Project archived | Yes — noop, log | 1.0 |
| 20 | `tenant_project_restart` | Project restarted | Yes — validate onboarding | 0.8 |
| 21 | `tenant_no_active_project` | All projects archived | No — monitor | 0.7 |
| 22 | `tenant_pending_restart_stale` | Restart pending >1h | No — escalate | 0.75 |
| 23 | `smoke_test_failures` | Smoke pass rate <80% | No — escalate | 0.75 |

Plus dynamically graduated patterns from Level 4 self-programming (loaded from `learned_patterns.json`).

---

## Diagnostic Report

The `/api/report` endpoint generates a complete diagnostic in one paste. Sections:

1. **Platform Overview** — daemon status, API health, CI green rate
2. **Infrastructure** — ECS services, task definitions, recent deployments
3. **Tenant Health** — per-tenant status with ground truth verification
4. **Ground Truth Deploy Detection** — actual HTTP checks against tenant app URLs, real PR/task counts from Neptune
5. **Active Heal Chains** — in-progress multi-step recoveries with current step
6. **Triage Decisions** — this cycle's decisions with confidence and actions
7. **Failure Patterns** — known patterns + learned candidates + graduation status
8. **SRE Metrics** — MTTD, MTTA, MTTR, MTBF, CFR, availability, error budget, antifragile score
9. **Project Lifecycle** — per-tenant project state (active, archived, restart, stale)

Tenant-specific reports available at `/api/report/tenant/{tenant_id}`.

---

## Ops Chat

Bedrock-powered conversational interface for platform operations. Endpoint: `POST /api/ops/chat`.

- **Model**: Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`) via Bedrock
- **Context injected**: full platform status, all tenant details, active heal chains, triage decisions, failure patterns, all 24 capabilities with blast radius
- **Execution**: model outputs `ACTION:capability_name:param=value` directives; chat system executes and returns results
- **Safety**: dangerous-blast-radius capabilities require explicit operator approval

---

## SRE Metrics

Calculated by `nexus/sensors/sre_metrics.py` and exposed at `GET /api/sre`.

| Metric | Description |
|--------|-------------|
| MTTD | Mean Time to Detect — seconds from event to detection (~30s per poll cycle) |
| MTTA | Mean Time to Acknowledge — seconds from detection to first action |
| MTTR | Mean Time to Recovery — seconds from detection to resolution |
| MTBF | Mean Time Between Failures — hours between incidents (168h window) |
| CFR | Change Failure Rate — fraction of deploys followed by incident within 30min |
| Availability | Uptime % = (window - downtime) / window × 100 |
| Error Budget | 99.9% SLO → 43.2 min/30 days allowed downtime; tracks consumed/remaining |
| Antifragile Score | Composite 0–100: patterns learned, match volume, graduation rate, heal success, MTTR, MTTD, availability, error budget |

---

## Project Lifecycle Monitoring

Tracks tenant project state changes and triggers appropriate responses.

| Event | Action | Auto? |
|-------|--------|-------|
| `archived` | Log only (noop) | Yes |
| `restart` | validate_tenant_onboarding | Yes |
| `no_active_project` | Monitor for abandonment | No |
| `pending_restart_stale` (>1h) | Escalate to operator | No |

---

## Learned Failure Patterns

Pattern graduation lifecycle:

1. **Incident** → triage can't auto-heal → escalates to operator
2. **Human resolves** → captures resolution via `POST /api/patterns/capture-resolution`
3. **Candidate created** → signature matching, confidence 0.3–0.5
4. **Next similar incident** → candidate matches, proposes heal capability
5. **Operator approves** → `POST /api/patterns/candidates/{name}/approve` → confidence +0.15, success_count +1
6. **Graduation** → 3 approvals → confidence >= 0.85, persisted to `learned_patterns.json`
7. **Autonomous** → auto-loaded into KNOWN_PATTERNS on startup, handled without human intervention forever

---

## Ground Truth Deploy Detection

Added April 11, 2026. Module: `nexus/sensors/ground_truth.py`.

Instead of trusting graph state alone, Overwatch verifies actual running state:

- `check_app_url(tenant_id)` — HTTP GET against the tenant's live app URL
- `get_full_pr_count(tenant_id)` — real PR counts from Neptune (no limits)
- `get_full_task_count(tenant_id)` — real task counts by status from Neptune
- `get_velocity(tenant_id)` — PR cycle time, completion rate, last activity
- `get_deploy_ground_truth(tenant_id)` — combines DeploymentProgress + app URL health
- `get_tenant_ground_truth(tenant_id)` — all of the above in one call

Used by triage to confirm health before and after healing actions.

---

## Sensor Modules

| Sensor | Purpose |
|--------|---------|
| `daemon_monitor` | Daemon cycle health, stale detection |
| `ci_monitor` | GitHub Actions green rate, failure details |
| `tenant_health` | Per-tenant pipeline health (tasks, PRs, tokens, repos) |
| `tenant_validator` | Full onboarding validation checklist |
| `capability_validator` | Per-tenant capability assessment (operational, degraded, blocked) |
| `capability_discovery` | Auto-discover Forgewing API endpoints (Level 5) |
| `performance` | Baseline tracking, anomaly detection for daemon cycles, PR gen time, velocity |
| `preemptive` | Token expiry prediction, capacity signals |
| `infrastructure_lock` | Prevent concurrent destructive operations |
| `sre_metrics` | MTTD/MTTA/MTTR/MTBF/CFR/availability/error budget/antifragile |
| `ground_truth` | Verify actual running state via HTTP + Neptune queries |

---

## Principles

1. **Overwatch never imports from aria-platform.** Physical separation, not just logical.
2. **Every action has a blast radius.** Safe = automatic. Moderate = auto with guardrails. Dangerous = approval required.
3. **Every decision is recorded.** The graph grows with every incident. Overwatch gets smarter.
4. **Prediction > Detection > Reaction.** Prevent problems, don't just fix them.
5. **Ian's interventions are training data.** Every manual action signals what Overwatch should learn to automate.
6. **Zero downtime is the standard.** Not aspirational — required.
7. **One person, unlimited customers.** Architecture scales to a thousand tenants with zero additional human effort.
