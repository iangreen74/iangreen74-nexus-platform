# Ontology — The Compounding Primitive

**Status:** Operational as of April 23, 2026. Three capture mechanisms feeding.
**Audience:** Engineers, investors, founders understanding the data model.

---

## What the Ontology Is

The ontology is Forgewing's compounding primitive. It's a tenant-scoped typed graph that accumulates everything a founder builds, decides, hypothesizes, and learns. Unlike a knowledge graph (general), this is specifically a **founder/company ontology** — scoped to one tenant's journey.

Every conversation, every deploy, every decision adds to it. ARIA reads from it on every response. The ontology is what makes ARIA a co-founder instead of a chatbot.

---

## Object Types (v0)

| Type | Fields | Status Values | Source |
|------|--------|---------------|--------|
| Feature | name, description, status | proposed, in_progress, shipped, deprecated, cancelled | Mechanism 1 (conversation) |
| Decision | name, context, choice_made, reasoning, alternatives_considered | active, revised, reversed | Mechanism 1 (conversation) |
| Hypothesis | statement, why_believed, how_will_be_tested | unvalidated, validating, confirmed, falsified, abandoned | Mechanism 1 (conversation) |

### Link Types (v0)

| Link | Semantics |
|------|-----------|
| motivates | Feature → Decision, Hypothesis → Feature |
| supersedes | Decision → Decision (revision chain) |
| validates | Experiment → Hypothesis |

### Known Gaps

- **No Bug type.** ARIA can't ground responses in bug history. Needed for the "calm under pressure when things break" persona commitment.
- **UserContext is product-focused** (`product_name`, `product_vision`, `target_users`), not founder-focused. No `founder_name`, `stage`, `prior_attempts`. The intimacy thesis requires schema evolution.
- **No Tradeoff, Experiment, Hire, InvestorUpdate, Customer, ArchitecturalChoice** types yet. These are in the ARIA_CAPTURE_PROTOCOL roadmap for Mechanisms 4-9.

---

## Storage Architecture

### Neptune Analytics (Layer 1 — graph)

Objects are stored as labeled nodes in Neptune Analytics graph `g-1xwjj34141`. Each object type has its own label (`Feature`, `Decision`, `Hypothesis`).

MERGE semantics: idempotent writes keyed by `(tenant_id, project_id, id)`. Repeated writes update properties without creating duplicates.

Queries use openCypher via `overwatch_graph.query()`.

### Postgres (Layer 2 — history)

Table `ontology_object_versions` stores every version of every object. Each MERGE to Neptune is preceded by a Postgres INSERT with the full object state + version number.

This gives:
- Full audit trail of every change
- Ability to reconstruct any point-in-time state
- Version diffs for "what changed" queries

### S3 Eval Corpus (Layer 3 — training)

Append-only ActionEvents in `s3://forgewing-eval-corpus-418295677815`. Every mutation (propose, update, accept, reject, edit) writes a JSONL event.

**Current status: substrate-only.** The bucket exists, the write policy exists, but no ActionEvents are actually flowing from the capture mechanisms yet. Loop 2 (weekly classifier improvement) is blocked until events flow.

---

## Capture Mechanisms as Sources

### Mechanism 1 — Conversation Classifier (LIVE)

Every founder conversation turn triggers Haiku extraction for Feature/Decision/Hypothesis candidates. Confident candidates (>0.6) are written to `classifier_proposals` as pending. The Lambda (`ontology-conversation-classifier`) subscribes to `forgewing-ontology-events` bus, `detail-type: conversation_turn`.

### Mechanism 2 — Deploy Event Classifier (LIVE)

Deploy outcomes (succeeded, failed, timeout, rolled_back) are classified for ontology relevance. The Lambda (`mechanism2-deploy-event-classifier`) subscribes to `forgewing-deploy-events` bus, filtering 5 detail types.

### Mechanism 3 — Socratic Proactive (LIVE)

Hourly scheduler scans the ontology for patterns that warrant questions. Doesn't write ontology objects — writes questions to `socratic_prompts` table. The questions surface in ARIA's prompt as "What you might want to think about."

### Mechanisms 4-9 — Roadmap

Git-native, meeting transcripts, standup templates, passive observability, external artifacts, write-then-link. See `ARIA_CAPTURE_PROTOCOL.md`.

---

## The Proposal Lifecycle

```
Mechanism extracts candidate
  → classifier_proposals (status=pending)
  → UI renders proposal card
  → Founder actions:
      Accept → POST /api/ontology/propose_object
             → Neptune MERGE + Postgres version write
      Reject → ActionEvent only (no ontology write)
      Edit   → Modified properties → same write path as Accept
```

### Two Write Paths

1. **Python direct:** `nexus.ontology.service.propose_object()` — used by `dispose()` in `proposals.py`
2. **HTTP:** `POST /api/ontology/propose_object` — wraps the same service function, used by the UI

Both paths: Postgres first (version history), then Neptune MERGE (current state), then S3 ActionEvent (audit trail).

---

## Cross-Repo Ontology Compounding

This is **why per-repo pricing is wrong**. A founder with three repos builds one ontology. The Feature "OAuth SSO" exists once, linked to the Decision "Use Passport.js" and the Hypothesis "Enterprise customers need SSO." Splitting by repo creates three partial ontologies that can't reference each other.

The ontology boundary is the tenant, not the repo. See `PRICING.md`.

---

## Future Schema Evolution

1. **Bug type** — track production issues, link to Features they affect
2. **Founder-facing UserContext** — `founder_name`, `stage`, `working_hours`, `communication_style`
3. **Tradeoff type** — explicit architectural tradeoffs with "accepted consequences"
4. **Multi-tenant isolation for enterprise** — team-scoped visibility within a tenant
5. **Ontology export** — founders own their data, can export the full graph

---

## Cross-References

- `STARTUP_ONTOLOGY.md` — strategic thesis and full type specifications
- `DATA_PLANE.md` — three-layer storage architecture
- `ARIA_INTELLIGENCE.md` — how the ontology feeds ARIA's prompts
- `PRICING.md` — why ontology scoping drives pricing architecture
- `nexus/ontology/service.py` — propose_object / update_object implementation
- `nexus/ontology/schema.py` — Feature/Decision/Hypothesis dataclasses
