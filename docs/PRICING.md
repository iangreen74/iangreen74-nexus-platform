# Pricing — Forgewing / VaultScaler Inc.

**Status:** Strategic framework locked April 23, 2026.
**Audience:** Investors, founders, internal alignment.

---

## Executive Summary

Forgewing pricing is **per-tenant base + per-seat scaling**. Repos are always free. The enterprise tier unlocks collaboration (multi-human ontology).

This is a deliberate architectural decision, not a pricing experiment. The ontology is tenant-scoped. Splitting pricing per-repo would split the ontology — destroying the compounding primitive that makes Forgewing valuable over time.

---

## Why Per-Repo Pricing Is Wrong

Most developer tools price per-repo or per-deployment because each repo is an independent unit of work. Forgewing is different: **one tenant = one founder's intelligence**. The ontology accumulates across all repos, all projects, all conversations. Splitting that by repo creates N partial ontologies instead of one complete one.

A founder who uses Forgewing for three repos builds one ontology three times richer than a founder with one repo. Charging per-repo punishes the behavior that makes the product most valuable.

**Repos are free. Always.**

---

## The Three Axes

### 1. Tenant (base)

Every founder gets a tenant. The tenant is the ontology boundary. Base price covers ARIA access, the capture pipeline (Mechanisms 1-3), the prompt assembly substrate, and the compounding memory (daily/weekly/monthly summaries).

### 2. Seat (scaling within tenant)

Additional humans within a tenant pay per-seat. Each seat gets ARIA access scoped to their permissions within the tenant. Seats share the tenant's ontology — this is the product's value proposition for teams.

### 3. Collaboration (enterprise tier)

Multi-team coordination within a single organizational tenant. ARIA mediates between teams: "Product team decided X, but Engineering hasn't seen it yet." This is the enterprise upsell — not more features, but ARIA as organizational memory.

---

## Pricing Tiers (Design Partner Phase)

| Tier | Price | Includes |
|------|-------|----------|
| Solo | $0-99/mo | 1 seat, 1 tenant, unlimited repos, full ARIA, full capture pipeline |
| Team | $X/seat/mo | 2-10 seats, shared ontology, ARIA-mediated handoffs |
| Enterprise | Negotiated | 10+ seats, multi-team, ARIA as org memory, custom integrations |

Design partner pricing (first 5): $0-99/month, Solo tier. Price discovery happens through design partner conversations, not guesswork.

---

## Investor Framing

**Net Revenue Retention (NRR) grows with headcount, not deployment volume.**

When a Solo founder hires their first engineer, they upgrade to Team. When the team grows to 10, they're on Enterprise. Each transition increases ARPU without the founder changing their behavior — the ontology is already there, already valuable, just shared with more people.

This is structurally different from:
- **Vercel** (per-deploy) — NRR grows with traffic, not team
- **GitHub** (per-seat/repo) — NRR grows with headcount but repos dilute value
- **Linear** (per-seat) — closest analog, but Linear's data doesn't compound

Forgewing's moat: **the ontology knows the founder after months/years, not minutes/hours.** Switching cost grows with ontology depth. By month 6, the ontology has hundreds of objects. By month 12, thousands. Replacing that is replacing institutional memory.

---

## Pricing Power Over Time

| Horizon | Ontology State | Pricing Dynamic |
|---------|---------------|-----------------|
| Month 1 | 10-50 objects | Low switching cost, high churn risk |
| Month 6 | 200-500 objects | Moderate lock-in, ontology has real value |
| Month 12 | 500-2000 objects | Strong lock-in, ARIA knows the founder deeply |
| Month 24 | 2000-5000 objects | Institutional memory, switching = amnesia |

This maps to standard SaaS cohort curves but with a data moat accelerant.

---

## Multi-Human Ontology Thesis (Future)

When two humans share a tenant, ARIA mediates:
- "Alice decided to use Postgres for auth on Monday. You're building the user model — should it reference that decision?"
- "The team shipped 3 features this week but hasn't validated any hypotheses. Want to talk about what you're assuming?"

This isn't chat. It's organizational cognition mediated by a co-founder who remembers everything.

---

## Open Questions

1. Multi-team-within-org permission model (who sees which ontology objects?)
2. ARIA-mediated coordination UX (notification surface? inline in conversation?)
3. Price sensitivity at each tier (design partner discovery)
4. Free tier sustainability (does $0 Solo work at 10K tenants?)

---

## Cross-References

- `BUSINESS.md` — company positioning, founding team, cap table
- `STARTUP_ONTOLOGY.md` — the compounding primitive that pricing protects
- `DATA_STRATEGY.md` — three data assets that justify pricing power
