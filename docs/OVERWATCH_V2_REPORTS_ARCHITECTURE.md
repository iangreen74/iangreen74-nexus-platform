# Overwatch v2 — Reports Architecture & Catalog

**Authored:** 2026-04-25 (Sprint 14 Week 1)
**Author:** Claude (CTO/Lead Engineer/Chief Data Architect)
**Status:** CANONICAL — Phase 2 detail spec for the substrate defined in [`OPERATIONAL_TRUTH_SUBSTRATE.md`](OPERATIONAL_TRUTH_SUBSTRATE.md)
**Parent:** [`OPERATIONAL_TRUTH_SUBSTRATE.md`](OPERATIONAL_TRUTH_SUBSTRATE.md) — the substrate doc supersedes the build-sequencing implied below; the report catalog itself is unchanged.

---

## Architectural foundation

**The core insight:** Reports are the substrate. Echo is the synthesis and action layer on top of reports. This separation is what makes the system scale from 5 tenants to 5,000.

**Three layers, in order of dependency:**

1. **Capture layer** — Cross-tenant read primitives. Fleet sweep service runs on schedule, captures structured state per tenant, writes to Postgres.
2. **Reports layer** — Pre-computed, structured views over captured data. The right-pane in Overwatch dashboard renders these. Reports are themselves first-class objects with versions, history, and diff capability.
3. **Synthesis layer** — Echo reads reports, synthesizes findings, recommends actions, executes under operator approval.

**Design language:** Borrowing from Ben's mockup wholesale — collapsible right pane (260-560px), mode toggle at top (becomes Plan / Live / **Reports**), structured cards within each mode, drilldown click pattern. Different colors/branding per Ian's call.

---

## Report catalog — 12 reports, organized by audience

I've grouped reports by *who's looking* and *what they need to know*. This matches how operations actually flows: top-of-funnel "is anything on fire" → mid-funnel "where are the problems" → drill-down "why is this specific thing broken" → action "what do I do about it."

### Tier 1: Operator-at-glance (the morning coffee view)

These are the reports Ian opens first thing each day. Every metric here should be a click-to-drilldown.

#### Report 1: Fleet Health Overview

**Audience:** Ian, daily, first 30 seconds of the day
**What it shows:**
- Total active tenants
- Tenants by health status: Green (deploying, healthy), Amber (degraded but functional), Red (broken / blocked)
- Trend over last 7 days — is the fleet getting healthier or worse?
- Top 3 most-active tenants (by deployment count, by ARIA conversation volume)
- Top 3 most-troubled tenants (by error rate, by failed deployments)

**Why this matters:** Without this, you can't tell if you have an emergency or a routine day. V1 had Goal/Feature/Tenant cards as the dashboard top — this is the V2 equivalent.

**Source data:** Fleet sweep service, runs hourly. Aggregates per-tenant state.

**Update cadence:** Hourly

---

#### Report 2: Critical Findings (last 24h)

**Audience:** Ian, ad-hoc, "what broke recently?"
**What it shows:**
- All critical-severity events across the fleet in the last 24 hours
- Each finding: tenant, timestamp, classifier, affected resources, current state (acknowledged / investigating / resolved)
- Auto-grouped by classifier (e.g., "deployment timeout" groups related findings even across tenants)

**Why this matters:** Lets you triage instead of being overwhelmed. The grouping is critical — if 5 tenants hit the same deployment timeout, that's ONE problem with 5 instances, not 5 problems.

**Source data:** Mechanism 2 deploy event classifier output, Mechanism 3 Socratic scheduler output, customer ECS/ALB telemetry from cross-tenant reads.

**Update cadence:** Realtime (event-driven)

---

#### Report 3: Pipeline Activity (last 24h)

**Audience:** Ian, daily
**What it shows:**
- All deployments attempted across the fleet, status: succeeded / failed / in-progress
- Success rate trend (rolling 7-day)
- Average deployment duration
- Failed deployments grouped by failure type (build error, deploy error, smoke fail, rollback)

**Why this matters:** Forgewing's value proposition is "deployments work." If success rate drops, customer trust drops. This report is the trust thermometer.

**Source data:** Deploy event classifier (Mechanism 2), customer GitHub Actions + CodeBuild via cross-tenant reads.

**Update cadence:** Realtime

---

### Tier 2: Per-tenant deep dive (the diagnose-this view)

When Tier 1 surfaces a problem tenant, you click through to these.

#### Report 4: Tenant Operational Profile

**Audience:** Ian, on-demand for a specific tenant
**What it shows:**
- Tenant identity: ID, customer name, plan, signup date, last activity
- ECS service states: cluster, services, task definitions, deployment state, healthy target counts
- ALB target health for tenant's services
- Recent deployments (last 10): status, duration, who triggered, commit SHA
- Recent CI runs: workflow, status, failure reasons if any
- Cost trajectory (if Cost Explorer integration enabled)
- Active conversations with ARIA (count, recent topics)
- Ontology object counts: Decisions, Hypotheses, Features, BriefEntries — and trend

**Why this matters:** When Ben asks "what's wrong with my tenant," this is the page you open. It's the operator's "single pane of glass" for a tenant.

**Source data:** Cross-tenant reads + ARIA conversation logs + ontology queries.

**Update cadence:** On-demand (refreshes when opened)

---

#### Report 5: Tenant Failure Diagnose (the V1 Diagnose button)

**Audience:** Ian, when a tenant is in red state
**What it shows:**
- Three-tier investigation (V1 parity):
  - **Tier 1: What's broken?** Concrete failure: service X has 0 healthy targets, deployment Y exit code 137, etc.
  - **Tier 2: Why is it broken?** Synthesized from logs, ALB target health checks, recent deployment changes, CodeBuild output
  - **Tier 3: What's the fix?** Recommended action — and if there's a Learned Pattern from a previous similar failure, surface that.
- Confidence score on the diagnosis
- Evidence list: every log line, metric, or finding that contributed to the diagnosis

**Why this matters:** This IS the report you said V1 had that "saved your asses." Goal-driven investigation, not ad-hoc curiosity. The operator clicks one button and gets a full diagnosis with fix recommendation.

**Source data:** Real-time cross-tenant reads + Bedrock Sonnet for synthesis + LearnedPattern lookup from ontology.

**Update cadence:** On-demand (computed at click time)

---

#### Report 6: Tenant Conversation Trajectory

**Audience:** Ian, post-incident or for ARIA quality review
**What it shows:**
- Last N conversations between this tenant and ARIA
- For each conversation: turn count, classifier proposals generated, accepted/rejected/edited, outcome (deployment? abandoned? help received?)
- Quality dimensions: did ARIA understand the question? Did the answer ground in real data? Did the recommendation work?
- Trajectory: is the founder making progress, or stuck?

**Why this matters:** Forgewing's promise is "ARIA helps non-technical founders ship software." If a tenant's trajectory shows them stuck or churning conversations without progress, that's a leading indicator of churn before it shows up in usage metrics.

**Source data:** ARIA conversation logs, classifier_proposals table, ontology object versions.

**Update cadence:** Realtime

---

### Tier 3: Fleet patterns (the "what's everyone doing" view)

These are aggregate reports across the whole fleet — they answer questions Tier 1 and Tier 2 can't.

#### Report 7: Cross-Tenant Failure Patterns

**Audience:** Ian, weekly review or when a pattern emerges
**What it shows:**
- Failure classifications grouped across the fleet
- For each pattern: how many tenants affected, total instances, first/last seen, suggested standard fix
- Highlights novel patterns (first occurrence in last 30 days)
- Highlights worsening patterns (frequency increasing week-over-week)

**Why this matters:** You said it yourself — at 1,000 tenants you can't ask 1,000 questions. You ask "what failure patterns are happening across the fleet" and the answer is in this report. THIS is what scales.

**Source data:** All capture data, with classification taxonomy applied (potentially trained over time).

**Update cadence:** Daily

---

#### Report 8: Compounding Loop Health

**Audience:** Ian, weekly — the most strategic report
**What it shows:**
- Per-tenant ontology object growth: Features, Decisions, Hypotheses, BriefEntries — over time
- Accretion rate: how fast is the ontology growing per tenant?
- Conversation grounding rate: what % of ARIA responses cite ontology objects?
- Cross-conversation context use: are subsequent ARIA responses better-grounded than earlier ones for the same tenant?
- The four leading indicators from your data strategy

**Why this matters:** This report answers the strategic question: **is the compounding loop actually working?** The whole thesis depends on this. If accretion is 0 or grounding rate is dropping, the moat isn't building.

**Source data:** Ontology Postgres + Neptune, ARIA prompt assembly logs.

**Update cadence:** Daily

---

#### Report 9: Goal Health (V1 parity)

**Audience:** Ian, daily — the top-of-dashboard scorecard
**What it shows:**
- "Is Forgewing achieving its purpose?" — synthesized from fleet trajectory
- Each major capability scored on success rate: ARIA onboarding, Quality Gate, Forge Engine deployments, Accretion Core, Deliberation Engine
- Trend: improving / stable / degrading
- Critical issues that prevent goal achievement (auto-promoted from Reports 2/3/7)

**Why this matters:** V1 had this as the dashboard top card. It's the "executive summary" of the whole platform. Whether to feel good or bad about the state of things in 30 seconds.

**Source data:** Aggregated from Reports 1-3 + capability success rates.

**Update cadence:** Daily

---

### Tier 4: Action-oriented reports (the "what should I do" view)

These reports don't just inform, they recommend.

#### Report 10: Recommended Actions Queue

**Audience:** Ian, daily
**What it shows:**
- Prioritized list of recommended actions across the fleet
- Each action: target tenant(s), recommended fix, confidence score, blast radius (single tenant / fleet-wide), estimated time
- Actions auto-recommended by Echo or pattern recognition
- Operator approval workflow: approve / modify / reject / defer

**Why this matters:** This is where reports become action. Echo synthesizes all the report data and proposes specific actions. You approve in one click. With mutation tools (Day 4 work), Echo executes.

**Source data:** All other reports + Echo synthesis + LearnedPattern library.

**Update cadence:** Daily, with realtime additions for critical actions

---

#### Report 11: Pattern-Based Action Plans

**Audience:** Ian, weekly
**What it shows:**
- "X tenants exhibit pattern Y. Apply standard fix Z to all of them?"
- Pattern: novel patterns (1-3 tenants), spreading patterns (3-10 tenants), epidemic patterns (10+ tenants)
- For each: detailed pattern description, recommended remediation, dry-run preview, blast radius warning
- Operator picks: apply to all / apply to selected / defer / never apply

**Why this matters:** This is the scale-out report. At 1,000 tenants you can't fix problems one at a time. You apply pattern-based fixes to groups. THIS is the operational primitive that lets one human run a 1,000-tenant fleet.

**Source data:** Pattern recognition over Reports 7 + LearnedPattern + cross-tenant impact analysis.

**Update cadence:** Daily

---

#### Report 12: Capability Gap & Investment Suggestions

**Audience:** Ian, monthly
**What it shows:**
- Reports / questions Echo could not adequately answer (logged each time Echo says "I don't have enough data")
- Capabilities Echo lacks (e.g., "could read X but not Y")
- Suggested next capability investments, ranked by frequency-of-need × estimated build cost
- Pattern: "If we built capability X, it would have helped Y times in the last 30 days"

**Why this matters:** Self-improving operator console. The system tells you what to build next based on what it's been blocked on. Strategic prioritization driven by data, not gut.

**Source data:** Echo conversation logs annotated with capability gaps + LearnedPattern misses + operator-flagged "wish I could ask X."

**Update cadence:** Monthly

---

## Build sequence (5 phases, ~3-4 sprint days)

### Phase 1: Capture primitives (Day 1, ~5 hours)

Build cross-tenant read tools — foundation for everything:
- `read_customer_tenant_state(tenant_id)` — ECS/ALB/CodeBuild/health
- `read_customer_pipeline(tenant_id)` — GitHub Actions, CodeBuild logs, deployment history
- `read_customer_ontology(tenant_id)` — Postgres + Neptune queries scoped to tenant
- `read_aria_conversations(tenant_id)` — ARIA conversation logs

These are Lambda-style tools Echo can invoke. Each tool has cross-account IAM via `forgescaler-*` role assume pattern.

**Definition of done:** Echo can answer "what's the state of tenant X?" with grounded data.

### Phase 2: Fleet sweep service (Day 1-2, ~3 hours)

Cron-driven Lambda runs hourly:
- Iterates all tenants
- Calls capture primitives for each
- Writes structured records to Postgres `tenant_state_snapshots` table
- Emits classifier events for state changes (delta detection)

**Definition of done:** Postgres has fleet-wide state data, refreshed hourly. Reports query this, not live AWS APIs.

### Phase 3: Reports API + UI shell (Day 2, ~6 hours)

- API endpoints: `GET /api/reports/<report_id>` returning structured JSON
- React UI: collapsible right-pane "Reports" mode (Plan / Live / Reports tabs)
- Six Tier 1 + Tier 2 reports rendered as cards
- Drilldown navigation between reports

**Definition of done:** Open Overwatch dashboard, switch to Reports mode, see real fleet data rendered in Ben's design language.

### Phase 4: V1-parity Diagnose (Day 3, ~4 hours)

- Three-tier investigation orchestrator (Step Functions)
- Reports 4, 5, 6 fully implemented
- LearnedPattern lookup integrated
- "Diagnose this tenant" button on Report 4 → triggers full investigation → renders Report 5

**Definition of done:** V1 reporting parity. Click Diagnose on a tenant card, get the same depth of diagnosis V1 produced.

### Phase 5: Pattern recognition + action queue (Day 3-4, ~6 hours)

- Reports 7, 10, 11 implemented
- Pattern classification job (daily cron)
- Action queue UI with approve/modify/reject workflow
- Foundation laid for mutation tools to execute actions (the actual mutation tools are Day 4 work)

**Definition of done:** System recommends actions; operator can approve them; foundation exists for autonomous execution.

### Phase 6 (post-launch): Strategic reports

Reports 8, 9, 12 are the strategic-tier reports. Lower priority because they don't affect day-to-day operations as directly. Schedule for Sprint 14 Week 3 or post-launch.

---

## Total time budget

- Phase 1: 5h
- Phase 2: 3h  
- Phase 3: 6h
- Phase 4: 4h
- Phase 5: 6h
- **Total: ~24 hours = 3-4 sprint days**

This is the V1 reporting parity + scalable pattern actions roadmap.

---

## What this gives you when complete

**At 5-15 tenants (design partner phase):**
- Open Overwatch each morning, see Fleet Health Overview
- Click any red tenant → get tenant deep dive
- Click Diagnose → get V1-parity investigation with fix recommendation
- Approve action → it executes (with mutation tools)
- Pattern detection runs in background, surfaces if anything novel

**At 100 tenants:**
- Same workflow, more data
- Pattern recognition becomes more valuable (more signal in cross-tenant data)
- Action queue becomes critical (can't manually triage 100 tenants)

**At 1,000 tenants:**
- Operator workflow is almost entirely report-driven
- Pattern-based actions handle 80% of issues without per-tenant attention
- Echo synthesizes from fleet-scale data
- Operator focuses on novel patterns, edge cases, and capability investment decisions

**This is the architectural primitive that lets one human run the company.**

---

## Decisions needed from Ian

1. **Build sequence: Phases 1-5 in the order proposed?** Or different priority order?
2. **Tier 1 reports first vs Tier 2 reports first?** I've ordered Tier 1 first because they're the "morning coffee" reports and benefit Ian immediately. Tier 2 is more powerful but used reactively.
3. **Reports API approach:** Server-rendered (Echo synthesizes each report on request) or pre-computed (fleet sweep populates, reports just query)? I've defaulted to pre-computed for scale; ad-hoc Echo reports work as a separate path.
4. **Mutation tools timing:** Phase 5 lays foundation but doesn't implement. Should we sequence mutation tools in this run, or defer to a separate Day 4 work?

---

## Methodology lesson candidate

**L38:** *Reports are the substrate that makes operator tooling scale. Conversational AI on top of reports scales infinitely; conversational AI as the primary interface scales to ~10 tenants. The architectural question for any operator tool is "what reports does this surface?" not "how good is the chatbot?" The chatbot is a layer; the reports are the foundation.*
