# Overwatch — Autonomous Platform Engineering for Forgewing

## What This Is
Overwatch is the external control plane for Forgewing (forgescaler.com).
It monitors, diagnoses, heals, engineers fixes for, and reports on the
Forgewing platform autonomously. The full architecture and roadmap live
in [`docs/OVERWATCH.md`](docs/OVERWATCH.md). NEXUS remains the daemon's
internal identity; **Overwatch** is the operator-facing brand.

## Architecture Principle
**Overwatch never imports from aria-platform.** It is a completely
separate system. It connects to Forgewing through:
- Neptune Analytics (read-only) — reads the Forgewing graph (`g-1xwjj34141`)
  via openCypher for tenant/task/PR/daemon-cycle data
- Neptune Analytics (read-write, dedicated label namespace) — Overwatch's
  *own* memory: PlatformEvents, FailurePatterns, HealingActions, Snapshots,
  Investigations, HumanDecisions. Same graph, distinct labels (`Overwatch*`).
- AWS APIs — ECS, CloudWatch, CloudFormation, Secrets Manager
- HTTP endpoints — health checks against forgescaler.com / api.forgescaler.com
- GitHub API — read aria-platform code, propose PRs (Forge engine)

If you ever find yourself adding `from aria...` or `import aria...` in this
repo, stop — that is a boundary violation. Route the need through an AWS,
Neptune, or GitHub call instead.

## The Layers

1. **Sensors** (`nexus/sensors/`) — detect events and health status, read-only.
   Each sensor returns a dict-shaped report and must never raise.
2. **Reasoning** (`nexus/reasoning/`) — triage events and reports, decide
   what to do. Produces `TriageDecision` objects with confidence + blast
   radius. The `alert_dispatcher` fires Telegram alerts on critical decisions
   with hourly per-key dedup.
3. **Capabilities** (`nexus/capabilities/`) — execute actions through a
   single registry that enforces rate limits, records outcomes, and gates
   on blast radius. No other module should bypass the registry to execute
   side effects.
4. **Memory** (`nexus/overwatch_graph.py`) — Overwatch's *own* graph store.
   Every triage decision becomes a `PlatformEvent`. Every capability call
   becomes a `HealingAction`. Every health check becomes a `TenantSnapshot`.
   Every known-pattern match increments a `FailurePattern`'s occurrence.
5. **Forge** (`nexus/forge/`) — Overwatch's ability to *modify* aria-platform.
   `aria_repo.py` reads files and opens labeled PRs. `fix_generator.py`
   templates known fixes. `deploy_manager.py` triggers deploys + rollbacks.
   All Forge actions are moderate-or-dangerous blast radius — gated.

## Key Constants
- AWS Account: 418295677815, us-east-1
- Neptune Analytics graph: `g-1xwjj34141` (shared with Forgewing, distinct labels)
- ECS Cluster: aria-platform
- Forgewing API: api.forgescaler.com
- Console: platform.vaultscaler.com (port 9001) — Overwatch's dashboard
- aria-platform repo: `iangreen74/aria-platform`
- Overwatch PR label: `overwatch-fix`

## Running Locally
```bash
NEXUS_MODE=local uvicorn nexus.server:app --port 9001
NEXUS_MODE=local python -m pytest tests/ -x -q
```
Local mode replaces every AWS / Neptune / HTTP call with mock data so the
full stack can be exercised without network access. The Overwatch graph
falls back to an in-memory dict-of-lists.

## Known Triage Patterns (seeded from real failures)
1. **github_permission_denied** → escalate (customer action needed)
2. **bedrock_json_parse** → auto-heal (non-blocking, retry with fence stripping)
3. **step_functions_access_denied** → escalate (IAM fix required)
4. **daemon_stale** → auto-heal (restart ECS service)
5. **ci_failing** → escalate with diagnosis

Adding a new pattern: add an entry to `KNOWN_PATTERNS` in
`nexus/reasoning/triage.py` and a matching test in `tests/test_triage.py`.
The matching pattern will be auto-recorded to the graph on first hit.

## Auto-Heal Safety Rules
Auto-healing fires only when all of these hold:
- `confidence >= 0.8`
- `blast_radius == "safe"`
- Hourly rate limit (`MAX_HEALING_ACTIONS_PER_HOUR = 10`) not exceeded

Any other case escalates via Telegram to the operator (with 1-hour
per-key dedup so the dashboard's 30s polling cadence doesn't spam).

## Forge Engine Capability Tiers
- **Tier 1 — Observe & Alert** (safe, automatic): read logs, metrics,
  graph state; send Telegram alerts; record events.
- **Tier 2 — Heal & Restore** (moderate, automatic with guardrails):
  restart ECS services, refresh tokens, retry, scale.
- **Tier 3 — Engineer & Modify** (dangerous, approval-gated): open PRs
  on aria-platform, update IAM, modify CFN, change task definitions.
  Confidence ≥ 0.95 OR explicit approval required.

See `docs/OVERWATCH.md` for the full roadmap (Phase 1 done, Phases 2-7
ahead).
