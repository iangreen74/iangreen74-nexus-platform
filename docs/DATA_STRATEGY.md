# Data Strategy

**Last updated: April 23, 2026 — three capture mechanisms operational**

---

## Core Principle

VaultScaler is a data company. Its defensible primitive is a proprietary ontology of each customer's operational world, plus a proprietary evaluation corpus defining quality for that customer. Both compound per-tenant over years. Everything else is scaffolding.

---

## The Three Compounding Assets

### 1. The Startup Ontology (Layer 2 — Primary)

**Status:** Operational. Three capture mechanisms feeding. Proposals flowing. Accept path verified end-to-end.

The ontology is the moat. It compounds per-tenant indefinitely. Never commoditized by foundation model improvements — competitors can match the model, not six months of accumulated founder intelligence.

**Object types (v0, deployed):** Feature, Decision, Hypothesis.

**Object types (v1, Sprint 15-22):** ArchitecturalChoice, Bug, Tradeoff, Customer, Experiment, Hire, InvestorUpdate.

**Known gaps (April 23):**
- No Bug type — ARIA can't ground responses in bug history
- UserContext is product-focused (product_name, product_vision), not founder-focused (no founder_name, stage)

**Capture mechanisms feeding the ontology:**

| Mechanism | Status | What it captures |
|-----------|--------|-----------------|
| M1 (conversation) | LIVE | Feature/Decision/Hypothesis from founder turns, 0.6 confidence threshold |
| M2 (deploy events) | LIVE | Deploy outcomes (succeeded/failed/timeout/rolled_back) |
| M3 (Socratic) | LIVE | Proactive questions based on ontology patterns (doesn't write objects — writes questions) |
| M4-M9 | Roadmap | Git-native, meetings, standups, observability, artifacts, write-then-link |

**Storage:** Neptune for current state (MERGE semantics), Postgres `ontology_object_versions` for version history, S3 eval corpus for audit trail.

### 2. The Eval Corpus (Layer 3 — Secondary)

**Status:** Substrate deployed. No ActionEvents actually flowing yet.

Append-only JSONL records in S3 (`forgewing-eval-corpus-418295677815`). Every ontology mutation (propose, update, accept, reject, edit) should write an event. The events accumulate into a training dataset for classifier improvement (Loop 2).

**Current reality:** The S3 bucket exists. The IAM write policy exists. The `write_action_event()` function exists. But no capture mechanism is actually calling it on the proposal lifecycle path yet. This is the most important gap to close for data strategy execution.

**When events flow:** Weekly review of disposition patterns (Loop 2) improves classifier prompts. Reject rate drops. Accept rate rises. The corpus compounds into better capture.

### 3. Operational Telemetry (Layer 1 — Scaffolding)

**Status:** Working. CloudWatch metrics, Neptune PlatformEvents, Overwatch graph.

Ephemeral. For debugging and analytics, not for compounding. Important for operations, not for moat.

---

## Four Leading Indicators

From CANONICAL.md. Honest current measurement as of April 23:

| Indicator | Target | Current | Assessment |
|-----------|--------|---------|------------|
| Graph depth per tenant | 50+ objects/tenant at 6mo | ~5 objects (early) | Measurable once proposals are accepted by founders |
| Ontology-linked action rate | 30%+ actions reference ontology | ~0% (capture just deployed) | Measurable post-proposal-card adoption |
| Multiplayer workspace adoption | 20%+ tenants multi-seat | 0% (no multi-seat yet) | Blocked on enterprise tier |
| Eval corpus accumulation | 100+ events/week | 0/week | **Gap: events not flowing** |

### Falsification

If by Q1 2027 graph depth is still <10 objects/tenant and eval corpus has <100 events total, the ontology-first thesis is falsified. Pivot to pure deployment tool.

---

## Why These Three Are Strictly Separated

- **Layer 1 (telemetry)** is ephemeral, high-volume, operational. Wrong to mix with tenant data.
- **Layer 2 (ontology)** is persistent, versioned, tenant-scoped. The product. Wrong to pollute with operational noise.
- **Layer 3 (eval corpus)** is append-only, immutable, cross-tenant aggregatable. Training fuel. Wrong to make it mutable or version-controlled.

Each layer has its own storage, its own access patterns, its own retention policy.

---

## Investor Narrative

"Our moat is three compounding assets, not three AI models."

1. The ontology compounds with every founder interaction
2. The eval corpus compounds with every disposition
3. The compression chain (daily→weekly→monthly) means ARIA's memory scales indefinitely

Competitors start at zero with every new customer. We start at zero too — but after six months, we're at 500+ objects. After a year, 2000+. After two years, the switching cost is amnesia.

---

## Cross-References

- `STARTUP_ONTOLOGY.md` — full type specifications and strategic thesis
- `DATA_PLANE.md` — technical storage architecture
- `ONTOLOGY.md` — capture mechanisms, proposal lifecycle, write paths
- `PRICING.md` — why data strategy drives pricing architecture
- `ARIA_INTELLIGENCE.md` — how the ontology feeds ARIA's prompts
