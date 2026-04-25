# OVERWATCH V2 — EXECUTION OPTIMIZATION PLAN

> **Status:** Companion to `OVERWATCH_V2_SPECIFICATION.md` (canonical).
> **Purpose:** Compress 14-day construction to 8 days wall-clock through dependency-aware parallelism, without compromising methodology.
> **Last updated:** 2026-04-24 evening, Sprint 14 Day 2.

---

## 0. What this document is and isn't

The specification is the contract. This document does not modify any architectural decision in the spec. It only optimizes the order and concurrency of construction.

If anything in this document conflicts with the spec, the spec wins.

---

## 1. The dependency graph

### 1.1 Critical path (sequential, cannot compress)

These have hard predecessor relationships. Each must complete before the next can begin.

```
[1] CFN templates authored
       ↓
[2] Provisioning deployed (Neptune + RDS + IAM + KMS + Secrets)
       ↓
[3] Schema definitions + Postgres migrations
       ↓
[4] Service layer (propose_object, dual-write Postgres-then-Neptune)
       ↓
[5] Tool registry foundation + first read tools
       ↓
[6] Reasoner skeleton (Bedrock loop + tool calling)
       ↓
[7] Chat backend wired to reasoner
       ↓
[8] First real task: v2 IAM scope fix end-to-end
```

Realistic critical-path duration with focused work and no waste: **5-6 days**.

### 1.2 Parallelizable workstreams

These have no dependency on each other and can run concurrently with each other and with later critical-path stages once their own predecessor has landed.

| Stream | Earliest start | Independent of |
|---|---|---|
| Phase 3 frontend bundle | Now (different repo) | Everything in nexus-platform |
| Persona authoring (you write `nexus/aria_v2/persona.md`) | Now | Everything else |
| CFN templates | Now | One another (5 separate stacks) |
| KMS approval-token module + JWT signing/verification | After [2] | Tools, reasoner |
| Pipeline-truth dashboard backend | After [2] | Tools, reasoner |
| Pipeline-truth dashboard frontend | After [2] | Reasoner, chat |
| Read-tool implementations | After [5] foundation | One another (each tool independent) |
| Write-tool implementations | After [5] + KMS module | One another |
| Sandbox tool (Fargate ephemeral) | After [2] | One another |
| V1 capability wrappers (30+) | After [5] | One another (embarrassingly parallel) |
| Chat surface frontend (left pane, stream, right pane, approval cards) | After [7] backend | Each other |
| Tests for each module | Alongside the module | One another |
| Documentation updates (CANONICAL, OVERWATCH, philosophy) | Alongside delivery | One another |

### 1.3 The parallelism multiplier

With one terminal running, you do critical-path-only work and the 14-day plan stands.

With **two terminals**, parallelizable streams run alongside the critical path. Compresses to ~10 days.

With **three terminals** (foundation work + tool/capability implementation + frontend), compresses to ~8 days.

Three is the practical ceiling — coordination overhead grows quadratically with terminal count and you become the bottleneck.

---

## 2. The 8-day compressed timeline

Each day shows three columns: critical path (Track B), parallel build (Track C), and frontend (Track A). Track A continues until the Phase 3 bundle ships, then queues for V2 takeover. Track C activates as soon as the critical path produces something parallelizable.

| Day | Track B (critical path) | Track C (parallel build) | Track A (Forgewing frontend) |
|---|---|---|---|
| **1 (tonight, Apr 24)** | Spec commit; CFN templates authored for Neptune + RDS + IAM (×2) + KMS + Secrets; all 5 stacks deployed in parallel; verify CREATE_COMPLETE | — (Track C activates Day 2) | Phase 3 left-pane bundle (the prompt already issued) |
| **2 (Fri Apr 25)** | Schema definitions (12 node types, 15+ edges) + Postgres migrations 007-011; service layer with dual-write; 50+ schema/service unit tests | Persona file authored (Ian); KMS approval-token module (issue + verify, single-use enforcement); Pipeline-truth backend endpoint scaffolded (raw boto3 reads, no tool layer dependency) | bundle merges if not done; otherwise ships |
| **3 (Sat Apr 26)** | Tool registry foundation; read tools (read_file, grep_repo, query_aws, read_cloudwatch_logs, query_neptune, query_postgres, read_secret, list_directory) | run_bash_sandbox via Fargate (ephemeral container, allowlist network); pipeline-truth backend completes against historical 96 SFN executions (acceptance test) | (queue: subsequent frontend work for V2) |
| **4 (Sun Apr 27)** | Mutation tools (propose_commit, execute_commit, create_pull_request, trigger_deploy, mutate_aws); KMS token verification wired into mutation role assumption | First batch of V1 capability wrappers (10 of 30+, parallelizable across one or two terminals) | — |
| **5 (Mon Apr 28)** | Reasoner skeleton: prompt assembly modeled on aria/prompt_assembly, seven-source priority order; first test (hardcoded user turn → tool call → response) | Remaining 20+ V1 capability wrappers; tests for each tool | — |
| **6 (Tue Apr 29)** | Reasoner full integration: conversation persistence, rolling memory (Haiku), tone calibration stub, ontology grounding via search_ontology | Pipeline-truth frontend (React component, color-coded status, validates against historical 96 SFN executions) | — |
| **7 (Wed Apr 30)** | Chat backend (POST /threads, /turns, /proposals, SSE streaming); chat frontend three-pane shell (route /engineering) | Approval card component with diff display; streaming message renderer with collapsed tool-call cards; conversation list left pane | — |
| **8 (Thu May 1)** | First real task: fix v2 IAM scope bug end-to-end through the chat. Investigation → CommitProposal → approve → execute → verify. All recorded in OverwatchGraph. | Documentation updates (CANONICAL, OVERWATCH, ENGINEERING_PHILOSOPHY); retrospective | — |

**Acceptance test on Day 8** is the spec's Section 14.8: Ian uses Overwatch V2 for the IAM scope fix without falling back to Claude Code. If that single fix flows end-to-end, V2 is operational.

**Buffer:** None. If a day slips, the compressed plan slips with it. The mitigation is not buffer — it's that parallelizing already absorbs the slack the 14-day plan implicitly carried.

---

## 3. Why this is honest, not optimistic

The 14-day plan in the spec assumes one stream of work running. Half the days in that plan have only one engineer-day of actual code in them; the other half is waiting (provisioning), reviewing (Day 1, Day 7), or polish (Day 14). With two-three terminals running, the wait days do parallel work, the review days are 30 min not 8 hours, and the polish day folds into Day 8.

This is not "cut corners." Every test still ships. Every CFN template is reviewed. Every tool gets unit tests. Every mutation flows through the approval gate. The spec's invariants hold.

What changes: **wall-clock days that contain only one engineer's worth of work become days containing two or three engineers' worth of work**, because parallel terminals run independent pieces. That's the only optimization.

---

## 4. Tonight (Apr 24, ~16:30, ~5 hours)

Two terminals.

**Terminal 1 (Track A — Forgewing frontend):**
Run the existing Phase 3 left-pane prompt (`PROMPT_TRACK_A_PHASE3.md`, already delivered). ~150 LOC across 7 small changes. ~1-2 hours including review.

**Terminal 2 (Track B — Overwatch V2 Day 1):**
Run the new tonight prompt (`PROMPT_TONIGHT_OVERWATCH_V2_DAY1.md`, delivered alongside this plan). Three things:

1. Branch creation + spec committed to `docs/OVERWATCH_V2_SPECIFICATION.md` and `docs/OVERWATCH_V2_EXECUTION_OPTIMIZATION_PLAN.md`
2. Five CFN templates authored under `infra/overwatch-v2/`:
   - `neptune-graph.yml` — OverwatchGraph with VPC routing
   - `rds-postgres.yml` — OverwatchPostgres with subnet group + security group + parameter group
   - `iam-reasoner-role.yml` — read-only role for the reasoner service
   - `iam-mutation-role.yml` — write role assumable only with valid approval token
   - `kms-approval-key.yml` — KMS key for JWT signing, key policy locked to mutation role
   - `secrets.yml` — Secrets Manager entries (GitHub PAT for code tools, etc.)
3. All stacks deploy in parallel; verification waits for CREATE_COMPLETE on all; resource ARNs/IDs land in `infra/overwatch-v2/outputs.json` for Day 2 work to consume

Provisioning wall-clock: ~20 minutes once templates are deployed (Neptune is the slowest, ~10 min). Template authoring is the bulk of the work — 2-3 hours of careful CFN engineering.

If Track B finishes early tonight, work starts on Day 2 schema definitions immediately. If it doesn't finish tonight, Day 2 morning resumes from the last clean stopping point.

---

## 5. Discrepancy log

Items in the spec that need a one-line confirmation from Ian before construction begins:

| Item | Spec says | Reality | Recommendation |
|---|---|---|---|
| Bedrock Sonnet model | 4.5 | Current code uses 4.6 (`OPS_CHAT_MODEL_ID`) | Update spec to 4.6; existing code is already calibrated |
| Persona file path | `nexus/aria_v2/persona.md` | New path; mirrors `nexus/aria/persona.md` | Confirm — will create the directory |
| Reasoner module path | `nexus/overwatch_v2/reasoner/...` | New path tree | Confirm |
| OverwatchGraph identifier | "to be allocated; reference name `overwatch-graph`" | CFN template will allocate | Will report ID after Day 1 provisioning |

These are not architectural disagreements; they are details to confirm before the templates land in the repo.

---

## 6. After Day 8

Day 9-10 (carryover from spec's Day 14 retrospective + transition):
- All documentation updates committed
- Claude Code chain demoted to fallback status
- All subsequent engineering work flows through V2
- Forgewing frontend track resumes through V2 instead of Claude Code

Sprint 14 itself continues. Design partner launch in late June / early July as the spec specifies. The two-week investment is repaid by every subsequent week.

---

*End of execution optimization plan.*
