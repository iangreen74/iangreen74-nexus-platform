# OVERWATCH AUTONOMY ROADMAP

## From Monitoring to Autonomous Engineering

**Created:** April 10, 2026
**Goal:** Take Overwatch from Level 1 (detect + single action) to Level 4 (self-programming) in one sprint.
**Repo:** iangreen74/iangreen74-nexus-platform

---

## Current State: Level 1 — Reactive Autonomy

What exists today:
- 8 sensors polling every 30 seconds
- 13 triage patterns from real incidents
- 15 registered capabilities (safe/moderate/dangerous)
- Executor with 4 safety gates (confidence, blast radius, cooldown, rate limit)
- Escalation dedup (30 min on executor, 1 hour on Telegram)
- SRE metrics (MTTD, MTTA, MTTR, MTBF, CFR, availability, antifragile score)
- Incident lifecycle (open → acknowledge → resolve)
- 2 learned failure patterns (ci_failing, daemon_stale)

What's missing: verification of heal outcomes, multi-step healing, performance trending, proactive drift detection, self-programming.

---

## Level 2 — Closed-Loop Verification + Heal Chains

### Philosophy
A senior SRE doesn't restart a service and walk away. They restart, wait, verify, and if it's still broken, try the next thing. Overwatch should work the same way.

### 2A: Heal Chains (ordered multi-step healing)

**Concept:** Each healable pattern defines a chain of actions, not just one. The executor works through the chain step by step, verifying between each step.

**Data model — new field on TriageDecision:**
```python
@dataclass
class HealChain:
    steps: list[HealStep]  # ordered actions to try
    current_step: int = 0

@dataclass 
class HealStep:
    capability: str         # registered capability name
    kwargs_builder: str     # function name to build kwargs
    verify_after_cycles: int  # how many poll cycles to wait before checking
    description: str        # human-readable ("Restart daemon", "Check code version")
```

**Example chain for daemon_stale:**
1. `restart_daemon` → wait 2 cycles (60s) → check daemon health
2. If still stale: `diagnose_daemon_timeout` → log which hook is slow
3. `check_daemon_code_version` → if drift detected, escalate with "daemon running old code"
4. If all steps fail: `escalate_to_operator` with full context from all prior steps

**Example chain for ci_failing:**
1. `retrigger_workflow` (most recent failed run) → wait 5 cycles (2.5 min)
2. `get_failing_workflows` → if same workflow still failing, escalate with step detail
3. If different workflow now failing: retrigger that one too (max 2 retriggers per incident)

**Example chain for empty_tenant_token:**
1. `refresh_tenant_token` → wait 1 cycle
2. `validate_tenant_onboarding` → verify token is now present
3. If still empty: escalate ("token refresh failed — installation_id may be invalid")

**Implementation plan:**
- New file: `nexus/reasoning/heal_chain.py` (~120 lines)
  - `HealChain`, `HealStep` dataclasses
  - `CHAINS` dict mapping pattern names to their chains
  - `get_chain(pattern_name)` → returns the chain or None
  - `advance_chain(incident_id, step_result)` → returns next step or "done"
- Modify `nexus/reasoning/executor.py`:
  - When executing a mapped action, check if the pattern has a chain
  - If yes, execute step 0, tag incident with `heal_chain_step=0, awaiting_verification=True`
  - On subsequent cycles, if incident has `awaiting_verification`, check if the sensor reports healthy
  - If healthy → resolve incident, record success, bump pattern confidence
  - If still unhealthy → advance to next step in chain
  - If chain exhausted → escalate with accumulated context from all steps
- Modify `nexus/overwatch_graph.py`:
  - Add `heal_chain_step`, `awaiting_verification`, `verification_due_at` fields to incident nodes
  - Add `record_heal_step(incident_id, step, result)` method

### 2B: Verification Loop

**Concept:** Every heal action is followed by automatic verification on the next relevant sensor cycle.

**Implementation:**
- In executor, after successful capability execution, set incident state to `awaiting_verification`
- Store `verify_source` (which sensor to check) and `verify_after` (timestamp)
- In the poll loop (`routes.py`), before running triage, check for `awaiting_verification` incidents
- If the sensor now reports healthy → resolve incident with `auto_healed=True, verified=True`
- If the sensor still reports unhealthy → advance heal chain

**Graph model additions:**
```
OverwatchIncident node gains:
  - heal_chain_name: str
  - heal_chain_step: int  
  - awaiting_verification: bool
  - verification_due_at: str (ISO)
  - heal_attempts: list[{step, capability, result, timestamp}]
```

### Deliverable
After Level 2, Overwatch doesn't just fire-and-forget. It works problems through a defined sequence, verifies each step, and only escalates after exhausting its options. Every successful heal chain increases the pattern's confidence. Every failed chain provides diagnostic context for the human.

---

## Level 3 — Performance Engineering

### Philosophy
Failures are the floor. The ceiling is performance optimization. Overwatch should detect slow degradation long before it becomes a failure, and actively improve system throughput.

### 3A: Performance Baselines

**Concept:** Track rolling metrics for every measurable dimension. Establish baselines. Detect drift.

**Metrics to track:**
| Metric | Source | Baseline Window | Alert Threshold |
|--------|--------|----------------|-----------------|
| Daemon cycle duration | DaemonCycle.duration_seconds | 7-day rolling avg | >2σ from mean |
| PR generation time | Task created_at → PR submitted_at | 7-day rolling avg | >2σ from mean |
| Quality gate pass rate | QualityGateResult nodes | 7-day rolling avg | <80% (currently ~95%) |
| Task velocity per tenant | Tasks completed per day | 7-day rolling avg | Drop to 0 for >24h |
| Accretion context size | Accretion sources returning data | Per-tenant snapshot | Drop by ≥2 sources |
| Bedrock latency | (needs wiring) | 7-day rolling avg | >2σ from mean |
| Hook execution time per hook | DaemonCycle hook timings | 7-day per-hook avg | Any hook >20s avg |

**Implementation plan:**
- New file: `nexus/sensors/performance.py` (~150 lines)
  - `compute_daemon_performance(hours=168)` → cycle duration stats (mean, p50, p95, trend)
  - `compute_pr_velocity(tenant_id, hours=168)` → PR generation time stats
  - `compute_task_velocity(tenant_id, hours=168)` → tasks/day with trend
  - `compute_quality_gate_rate(tenant_id, hours=168)` → pass rate
  - `compute_context_health(tenant_id)` → accretion sources active vs expected
- New file: `nexus/sensors/performance_baselines.py` (~100 lines)
  - `PerformanceBaseline` dataclass (metric, mean, stddev, p50, p95, sample_count, window_hours)
  - `compute_baseline(metric_name, values)` → PerformanceBaseline
  - `is_anomalous(current_value, baseline)` → bool (>2σ)
  - `trend_direction(values)` → "improving" | "stable" | "degrading"
- New graph node type: `PerformanceSnapshot`
  - Written every poll cycle (or every 10 cycles to save writes)
  - Properties: metric_name, value, tenant_id (optional), timestamp
- Dashboard additions:
  - New "Performance" section showing trends per metric
  - Sparklines or simple trend arrows (↑ improving, → stable, ↓ degrading)

### 3B: Proactive Performance Alerts

**Concept:** When a metric drifts beyond threshold, generate a triage event before it becomes a failure.

**New triage patterns:**
```python
{
    "name": "daemon_cycle_drift",
    "match": lambda e: (
        e.get("type") == "performance_alert"
        and e.get("metric") == "daemon_cycle_duration"
        and e.get("anomalous") is True
    ),
    "action": "diagnose_daemon_timeout",
    "blast_radius": BLAST_SAFE,
    "confidence": 0.85,
    "reasoning": "Daemon cycle duration trending above baseline — investigating before it becomes a stall.",
    "diagnosis": "Cycle duration anomaly detected.",
    "resolution": "Run diagnose_daemon_timeout to identify the slow hook.",
}
```

Similar patterns for:
- `pr_generation_slowdown` → investigate task executor / Bedrock latency
- `quality_gate_degradation` → trigger convention re-extraction
- `tenant_velocity_drop` → proactive engagement alert
- `context_health_decline` → trigger accretion source refresh

### 3C: Active Performance Improvement

**Concept:** Don't just detect problems — fix them. When daemon cycles slow down because a hook is taking too long, Overwatch should auto-tune.

**Capabilities to add:**
- `adjust_hook_frequency(hook_name, new_frequency)` — tell the daemon to run a hook less often
  - Requires: a new endpoint on the Forgewing API, or a Neptune node the daemon reads
  - Blast radius: MODERATE (changes daemon behavior)
- `trigger_convention_refresh(tenant_id)` — re-extract coding conventions
  - Requires: POST to Forgewing API
  - Blast radius: SAFE (read-only analysis)
- `trigger_accretion_rebuild(tenant_id)` — force regeneration of stale accretion sources
  - Blast radius: MODERATE

### Deliverable
After Level 3, Overwatch doesn't wait for the system to break. It watches performance curves, detects drift before humans notice, and actively tunes the system to maintain or improve throughput. The abstract expressionism principle applies: the customer never sees any of this. Their PRs just keep coming faster and more accurately.

---

## Level 4 — Pattern Graduation (Self-Programming)

### Philosophy
The antifragile thesis in its purest form. Every incident that requires human intervention should eventually become an incident that Overwatch handles autonomously. The system literally learns from you and programs itself to handle more.

### 4A: Resolution Capture

**Concept:** When you manually resolve an incident (via Ops Chat, CLI, or direct action), Overwatch captures what you did and stores it as a candidate pattern.

**Implementation:**
- Add "Resolve" button to each open incident on the dashboard
- Resolution form captures:
  - What action was taken (free text or select from capabilities)
  - Root cause (free text)
  - Was the existing pattern's diagnosis correct? (yes/no)
  - Should this be auto-healable in the future? (yes/no)
- Store as `HumanDecision` node in the graph (method already exists: `record_human_decision`)
- New method: `record_resolution(incident_id, action_taken, root_cause, should_auto_heal)`

### 4B: Candidate Pattern Generation

**Concept:** From a resolution, generate a candidate triage pattern that could handle this incident next time.

**Implementation:**
- New file: `nexus/reasoning/pattern_learner.py` (~120 lines)
  - `generate_candidate_pattern(incident, resolution)` → CandidatePattern
  - Uses the incident's sensor data + triage decision + resolution to construct:
    - A match function (based on the sensor signals that triggered the incident)
    - An action (the capability that resolved it)
    - Confidence (starts at 0.5 — must earn its way up)
    - Diagnosis and resolution text from the human input
  - Candidate patterns are stored in the graph as `CandidatePattern` nodes
  - They appear on the dashboard in a "Pattern Candidates" section

### 4C: Pattern Promotion

**Concept:** A candidate pattern that matches the same incident signature N times (configurable, default 3) and has been verified by the human each time gets promoted to a real pattern.

**Promotion criteria:**
1. Same signature matched ≥3 times
2. Human approved the auto-heal each time
3. Blast radius is SAFE or MODERATE
4. Confidence has been manually set to ≥0.8

**Implementation:**
- In triage, check candidate patterns AFTER known patterns (they're lower priority)
- When a candidate matches, surface it as "NEXUS wants to try: [action] — Approve?"
- If approved and the heal works, increment the candidate's success count
- When success_count ≥ 3, graduate to KNOWN_PATTERNS
- Graduation means: write the pattern to a `learned_patterns.json` file that triage.py loads on startup
- The pattern is now permanent — it survives restarts and deploys

### 4D: The Self-Programming Loop

**The full cycle:**
1. Incident occurs → no known pattern matches → escalate to human
2. Human resolves it → resolution captured
3. Candidate pattern generated from resolution
4. Same incident recurs → candidate pattern matches → proposes heal
5. Human approves → heal succeeds → candidate gains confidence
6. After 3 successful approvals → pattern graduates to permanent
7. Same incident recurs → now auto-healed without human involvement
8. System is now stronger than before the first incident

**This is the antifragile loop made concrete.** Every incident makes Overwatch smarter. The antifragile score should reflect this: each graduated pattern adds points to the score.

### Deliverable
After Level 4, Overwatch programs itself. The more incidents it sees, the more it can handle autonomously. The human is the teacher, not the operator. Over time, the escalation rate drops toward zero — not because there are fewer problems, but because Overwatch has learned to handle them all.

---

## Execution Order

### Sprint 1: Level 2A + 2B — Heal Chains + Verification (~2 prompts)
**Files:** heal_chain.py (new), executor.py (modify), overwatch_graph.py (modify)
**Test:** daemon_stale → restart → verify → resolve. ci_failing → retrigger → verify → resolve.

### Sprint 2: Level 3A — Performance Baselines (~1 prompt)
**Files:** performance.py (new), performance_baselines.py (new), routes.py (add endpoints), index.html (dashboard section)
**Test:** daemon cycle duration baseline computes. Trend detection works.

### Sprint 3: Level 3B + 3C — Proactive Alerts + Active Tuning (~1 prompt)
**Files:** triage.py (new patterns), performance.py (anomaly detection), new capabilities
**Test:** inject a slow hook → Overwatch detects drift → diagnoses → recommends fix.

### Sprint 4: Level 4A + 4B — Resolution Capture + Candidate Patterns (~1 prompt)
**Files:** pattern_learner.py (new), routes.py (resolution endpoints), index.html (resolution UI), overwatch_graph.py (candidate pattern nodes)
**Test:** manually resolve an incident → candidate pattern appears on dashboard.

### Sprint 5: Level 4C + 4D — Pattern Promotion + Self-Programming (~1 prompt)  
**Files:** pattern_learner.py (promotion logic), triage.py (candidate matching), learned_patterns.json
**Test:** approve candidate 3 times → pattern graduates → next occurrence auto-healed.

### Sprint 6: Dashboard Polish
**Files:** index.html (performance section, pattern candidates, resolution UI, heal chain visualization)
**Test:** full visual verification of all new sections.

---

## Success Criteria

| Level | Metric | Target |
|-------|--------|--------|
| 2 | Heal chains fire and verify | daemon_stale resolved without escalation in <3 cycles |
| 2 | Verification loop closes incidents | Auto-resolved incidents appear in SRE metrics |
| 3 | Performance baselines computed | Dashboard shows trend for daemon cycle time |
| 3 | Proactive alerts fire | Drift detected before failure threshold |
| 4 | Candidate patterns generated | ≥1 candidate from a manual resolution |
| 4 | Pattern graduation works | ≥1 pattern promoted from candidate to permanent |
| 4 | Antifragile score increases | Score rises with each graduated pattern |

---

## Principles Throughout

**Abstract Expressionism:** None of this is visible to Forgewing customers. Their experience just silently improves — PRs arrive faster, outages resolve before they notice, the system gets smarter every day.

**Antifragile Engineering:** Every sprint makes the system measurably stronger. The antifragile score is not vanity — it's a real measure of how many failure modes the system can now handle autonomously.

**Closed-Loop:** No fire-and-forget. Every action is verified. Every escalation is a learning opportunity. Every resolution feeds back into the system.

**Performance > Uptime:** Uptime is table stakes. The real competitive advantage is a system that actively optimizes itself — faster daemon cycles, better PR quality, higher tenant engagement.

---

## Level 5 — Auto-Discovery (April 10)

Overwatch no longer needs manual sensor updates when Forgewing adds features.

**capability_discovery.py** probes known endpoint patterns every ~5 minutes:
- Core: /health
- Deployment: deploy-progress, deployment-dna, deploy-preview, deployment-intelligence
- QA: smoke-test
- Tenant: status, onboarding, conversation, tasks

New endpoints are detected automatically and logged as `capability_discovered` events. The dashboard shows all discovered capabilities and their status.

When a new endpoint appears (e.g., /visual-regression/{tid}), Overwatch starts monitoring it on the next discovery cycle without a code change.
