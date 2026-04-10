# OVERWATCH — Autonomous Platform Engineering for Forgewing

## Document Purpose
This is the architecture and roadmap for Overwatch, the autonomous platform engineering system that manages Forgewing. Overwatch has one job: ensure Forgewing runs perfectly so that one human (Ian) can operate a global product without hiring engineers.

## The Hierarchy
```
Ian (CEO) → manages Overwatch (the only system he touches)
Overwatch → manages Forgewing (zero human intervention needed)
Forgewing → serves customers (zero downtime, continuous experience)
```

Each layer only worries about one thing. Ian never thinks about Forgewing's stability because Overwatch handles it. Customers never experience downtime because Overwatch prevents it before it happens.

## Identity
- **Name**: Overwatch
- **URL**: platform.vaultscaler.com
- **Repo**: iangreen74/iangreen74-nexus-platform (to be renamed)
- **ECS Service**: aria-console (dedicated task def, nexus-platform ECR image)
- **Relationship to NEXUS**: NEXUS remains the daemon's internal identity. Overwatch is the external control plane that watches everything including NEXUS.

## Two Monitoring Domains
1. **Platform Health** (the factory) — Is the Forgewing system running? Daemon alive, API responding, CI green, infrastructure intact, costs within budget.
2. **Tenant Health** (the products) — Are customers being served well? PRs flowing, deployments healthy, conversations active, no stuck tasks, no permission errors.

`aria-platform` is never a tenant. It is the platform being monitored.

---

## THE INTELLIGENCE ARCHITECTURE

Mirroring Forgewing's three pillars + Omniscience stack, but applied to platform operations instead of code generation.

### Pillar 1: Overwatch Accretion Core

**Forgewing's Accretion Core** accumulates context about customer codebases across 12 sources. **Overwatch's Accretion Core** accumulates context about the platform itself.

Sources (growing over time):
1. **Platform Events** — every deployment, restart, scaling event, config change
2. **Failure History** — every error, its diagnosis, the resolution, the outcome
3. **Healing Actions** — every auto-heal executed, its blast radius, success/failure
4. **Tenant Patterns** — per-tenant behavioral patterns (PR velocity trends, conversation frequency, deployment cadence)
5. **Cost Signals** — Bedrock spend, ECS hours, Neptune queries, S3 storage — all tracked per tenant
6. **CI/CD History** — build times, failure rates, flaky tests, deployment frequency
7. **Performance Baselines** — API latency p50/p95/p99, daemon cycle duration, Neptune query time
8. **Capacity Signals** — ECS task utilization, Neptune storage, Bedrock throttling
9. **Human Decisions** — every time Ian makes a manual decision, record it as training data for future automation
10. **Predictive Signals** — token expiry timelines, certificate renewals, quota approaching

Each source compounds over time. After 30 days of operation, Overwatch knows: "Monday mornings have 2x Bedrock latency, tenant X generates large PRs that take 3 cycles, deployment stacks in us-east-1a are 30% slower." This context feeds every decision.

### Pillar 2: Overwatch Deliberation Engine

**Forgewing's Deliberation Engine** spawns investigations when code generation confidence is low. **Overwatch's Deliberation Engine** spawns diagnostic workflows when platform confidence is low.

When Overwatch encounters an event it doesn't recognize:
1. **Observe** — gather all available signals (logs, metrics, graph state, recent changes)
2. **Hypothesize** — generate multiple possible explanations (using the Accretion Core's failure history)
3. **Test** — run targeted diagnostics to narrow hypotheses (check specific logs, query specific metrics)
4. **Conclude** — select the most likely diagnosis with confidence score
5. **Decide** — auto-heal if confidence ≥ 0.8 and blast radius is safe, otherwise escalate to Ian

Investigation types:
- **Service Investigation** — why is this ECS service unhealthy? (check task status, container logs, health endpoint, recent deployments)
- **Tenant Investigation** — why is this tenant stuck? (check GitHub permissions, Bedrock errors, task state, conversation history)
- **CI Investigation** — why is this workflow failing? (check runner status, dependency changes, test output)
- **Cost Investigation** — why did spending spike? (check Bedrock invocations, data transfer, NAT gateway costs)
- **Performance Investigation** — why is latency elevated? (check Neptune query patterns, Bedrock response times, ECS CPU/memory)

### Pillar 3: Overwatch Forge Engine

**Forgewing's Forge Engine** deploys customer code to AWS. **Overwatch's Forge Engine** modifies the Forgewing platform itself.

Capability tiers (mirroring Forgewing's Tier 1/2/3):

**Tier 1 — Observe & Alert** (safe, always automatic)
- Read logs, metrics, graph state
- Send Telegram/Slack alerts
- Record events in Overwatch's own graph
- Generate diagnostic reports

**Tier 2 — Heal & Restore** (moderate, automatic with guardrails)
- Restart ECS services (force new deployment)
- Refresh expired tokens
- Clear stuck tasks/investigations
- Scale services up/down
- Retry failed operations
- Rate limited: max 10 actions/hour, max 3 per service per hour

**Tier 3 — Engineer & Modify** (dangerous, requires approval or high confidence)
- Open PRs on aria-platform to fix recurring bugs
- Update IAM policies
- Modify CloudFormation stacks
- Change ECS task definitions
- Update Neptune schema
- Deploy new versions
- Approval gate: confidence ≥ 0.95 OR explicit Ian approval via Telegram/dashboard

### The Omniscience Stack

Five intelligence layers, each building on the previous:

**Layer 1: Temporal Intelligence**
Track the platform's behavior over time. Know that "the daemon has been cycling every 88-95 seconds for 3,600 cycles" so that a sudden 200-second cycle is anomalous. Build timelines for every tenant: when they onboarded, when their first PR landed, when they last interacted, their deployment history.

**Layer 2: Intent Inference**
Understand what Ian is trying to do. When Ian deploys a new version, Overwatch knows to watch for regressions. When Ian onboards a new tenant, Overwatch pre-checks their GitHub permissions, validates their repo structure, and predicts likely failure modes before they happen.

**Layer 3: Predictive Generation**
Predict problems before they occur:
- Token expires in 2 hours → refresh now
- Tenant hasn't interacted in 3 days → they may be stuck, check their pipeline
- CI green rate dropping → a flaky test is emerging, investigate before it blocks deploys
- Bedrock costs trending up → a tenant's repo is large, context window approaching limit
- Daemon cycle time increasing → Neptune queries are slowing, check graph size

**Layer 4: Pattern Recognition**
Recognize failure patterns across tenants and across time:
- "Every time a new tenant connects a private repo, the first PR fails with permission denied" → preemptively verify push access before attempting PR
- "Bedrock JSON parse errors cluster around large diffs" → auto-split large PRs before generation
- "Deployments fail on the first attempt 15% of the time but succeed on retry" → auto-retry deployments

**Layer 5: Autonomous Engineering**
The capstone. Overwatch doesn't just detect, diagnose, and heal — it engineers solutions:
- Identifies a recurring failure pattern
- Writes a fix (code change, config update, infrastructure modification)
- Tests the fix (runs the test suite, deploys to staging)
- Proposes the fix to Ian (or auto-merges if confidence is high enough)
- Monitors the fix in production
- Records the outcome for future learning

This is the closed loop. Problems generate solutions. Solutions generate learning. Learning prevents future problems.

---

## OVERWATCH'S OWN INFRASTRUCTURE

### Graph Database
Overwatch gets its own Neptune instance (or a dedicated prefix in the existing one) for:
- PlatformEvent nodes (deployments, restarts, config changes)
- FailurePattern nodes (error signature, diagnosis, resolution, success rate)
- HealingAction nodes (action taken, blast radius, outcome, timestamp)
- TenantHealthSnapshot nodes (periodic snapshots for trending)
- DiagnosticInvestigation nodes (hypothesis, evidence, conclusion)
- HumanDecision nodes (what Ian decided and why — training data)

### Event Bus
EventBridge rules feeding SQS for real-time event detection:
- CloudWatch alarm state changes
- ECS task state changes
- GitHub webhook deliveries (CI completion, PR events)
- Neptune slow query alerts
- Billing threshold alerts

### Capability Registry
Every action Overwatch can take is registered with:
- Blast radius classification (safe/moderate/dangerous)
- Rate limit
- Required confidence threshold
- Dry-run mode
- Rollback procedure
- Success/failure criteria

---

## IMPLEMENTATION ROADMAP

### Phase 1: Foundation (DONE — April 9, 2026)
- [x] Separate repo (iangreen74/iangreen74-nexus-platform)
- [x] Three-layer architecture (sensors, reasoning, capabilities)
- [x] Dashboard live at platform.vaultscaler.com
- [x] Real Neptune/ECS/CloudWatch data flowing
- [x] 4 triage patterns seeded from Ben's real failures
- [x] 4 capabilities registered with blast radius

### Phase 2: Sensor Accuracy (Next)
- [ ] Fix tenant health thresholds (CF stack naming, deployment detection)
- [ ] Filter out aria-platform from tenant list (it's the platform, not a tenant)
- [ ] Tune daemon stale threshold based on actual cycle patterns
- [ ] Wire Telegram alerts for critical triage events
- [ ] Add performance baselines (API latency, daemon cycle duration)

### Phase 3: Accretion Core (Week 1-2)
- [ ] Overwatch's own graph schema (PlatformEvent, FailurePattern, HealingAction)
- [ ] Record every triage decision as a node
- [ ] Record every healing action with outcome
- [ ] Build failure pattern library from real incidents
- [ ] Start tracking cost signals per tenant

### Phase 4: Deliberation Engine (Week 2-3)
- [ ] Diagnostic investigation workflows
- [ ] Multi-hypothesis reasoning for unknown failures
- [ ] Automated log analysis (CloudWatch → diagnosis)
- [ ] Automated metric correlation (latency spike → root cause)

### Phase 5: Predictive Intelligence (Week 3-4)
- [ ] Token expiry prediction and proactive refresh
- [ ] Tenant engagement prediction (will they churn?)
- [ ] CI failure prediction (flaky test detection)
- [ ] Cost forecasting and anomaly detection
- [ ] Capacity planning (when do we need to scale?)

### Phase 6: Autonomous Engineering (Month 2)
- [ ] Overwatch opens PRs on aria-platform for recurring fixes
- [ ] Automated test generation for detected failure modes
- [ ] Self-healing CI pipelines
- [ ] Autonomous scaling (scale up before demand, scale down at night)

### Phase 7: Omniscience (Month 3+)
- [ ] Cross-domain pattern recognition (tenant behavior × platform health × cost × performance)
- [ ] Predictive tenant onboarding (pre-validate everything before first PR)
- [ ] Self-optimizing infrastructure (continuously tune thresholds, resource allocation)
- [ ] Ian's decisions feed back into Overwatch's learning (every manual intervention becomes training data)

---

## THE BEN TEST

The measure of Overwatch's maturity is simple: if a new Ben signs up tomorrow, what happens?

**Today (Phase 1):** Overwatch detects the failure after it happens, shows it on the dashboard, but Ian still has to diagnose and fix manually.

**Phase 3:** Overwatch detects the failure, diagnoses it ("GitHub App not installed on customer account"), and sends Ian a Telegram with the diagnosis and suggested resolution.

**Phase 5:** Overwatch predicts the failure before it happens. During onboarding, it checks whether the customer has installed the GitHub App and, if not, ARIA guides them through it before the first PR is attempted. The failure never occurs.

**Phase 7:** Overwatch has seen this pattern across 50 tenants. The onboarding flow has been automatically patched to always verify GitHub App installation. The failure is architecturally impossible. Ian never knew there was a potential problem.

That's the journey from reactive to omniscient.

---

## PRINCIPLES

1. **Overwatch never imports from aria-platform.** Physical separation, not just logical.
2. **Every action has a blast radius.** Safe actions are automatic. Dangerous actions require approval.
3. **Every decision is recorded.** The graph grows with every incident. Overwatch gets smarter.
4. **Prediction > Detection > Reaction.** The goal is to prevent problems, not just fix them.
5. **Ian's interventions are training data.** Every time Ian acts manually, that's a signal that Overwatch should learn to handle that case.
6. **Zero downtime is the standard.** Not aspirational — required. Overwatch exists to make this true.
7. **One person, unlimited customers.** The architecture must scale to a thousand tenants with zero additional human effort.
