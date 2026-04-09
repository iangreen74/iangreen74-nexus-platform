# NEXUS Platform — Autonomous Operations for Forgewing

## What This Is
NEXUS Platform is the control plane for Forgewing (forgescaler.com). It monitors,
diagnoses, heals, and reports on the Forgewing platform autonomously.

## Architecture Principle
**NEXUS never imports from aria-platform.** It is a completely separate system.
It connects to Forgewing through:
- Neptune (read-only) — reads the Forgewing graph for tenant/task/PR data
- AWS APIs — reads ECS status, CloudWatch logs, CloudFormation stacks
- HTTP endpoints — health checks against forgescaler.com and api.forgescaler.com
- Secrets Manager — reads credentials (never writes Forgewing secrets)

If you ever find yourself adding `from aria...` or `import aria...` in this
repo, stop — that is a boundary violation. Route the need through an AWS or
Neptune call instead.

## Three Layers
1. **Sensors** (`nexus/sensors/`) — detect events and health status, read-only.
   Each sensor returns a dict-shaped report and must never raise.
2. **Reasoning** (`nexus/reasoning/`) — triage events and reports, decide what
   to do. Produces `TriageDecision` objects with confidence and blast radius.
3. **Capabilities** (`nexus/capabilities/`) — execute actions through a single
   registry that enforces rate limits, records outcomes, and gates on blast
   radius. No other module should bypass the registry to execute side effects.

## Key Constants
- AWS Account: 418295677815, us-east-1
- Neptune: g-1xwjj34141
- ECS Cluster: aria-platform
- Forgewing API: api.forgescaler.com
- Console: platform.vaultscaler.com (port 9001)

## Running Locally
```bash
NEXUS_MODE=local uvicorn nexus.server:app --port 9001
NEXUS_MODE=local python -m pytest tests/ -x -q
```
Local mode replaces every AWS/Neptune/HTTP call with mock data so the full
stack can be exercised without network access.

## Known Triage Patterns (seeded from real failures)
1. **GitHub permission denied** → escalate (customer action needed)
2. **Bedrock JSON parse failure** → auto-heal (non-blocking, retry with fence stripping)
3. **Daemon stale** → auto-heal (restart ECS service)
4. **CI failing** → escalate with diagnosis

Adding a new pattern means adding an entry to `KNOWN_PATTERNS` in
`nexus/reasoning/triage.py` and a matching test in `tests/test_triage.py`.

## Auto-Heal Safety Rules
Auto-healing fires only when all of these hold:
- `confidence >= 0.8`
- `blast_radius == "safe"`
- Hourly rate limit (`MAX_HEALING_ACTIONS_PER_HOUR`) not exceeded

Any other case escalates via Telegram to the operator.
