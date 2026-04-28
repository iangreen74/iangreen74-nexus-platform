# OperatorFeature bootstrap runbook (Sprint 15 Day 4, 2026-04-28)

## Why this runbook exists

OperatorFeature definitions ship as Python module-level constants under
`nexus/operator_features/instances/`. Until they're written to Neptune
(a `MERGE` against the `OperatorFeature` label), the Phase 0e.2 report
engine returns the "not found" stub. Phase 0e.4's first production
rendering on 2026-04-28 surfaced this gap — the artifact-vs-persistence
pattern that bit the classifier Lambda for 4 days earlier in the
sprint, recurring in a different domain.

PR-G1 ships `scripts/bootstrap_operator_features.py` as the explicit,
audit-trailed mechanism. PR-G2 will generalise this into a server.py
startup hook so future instances bootstrap automatically.

## When to run this script

- After PR-G1 merges, once, to bootstrap `ontology_capture_loop`.
- After any subsequent PR adding an instance under
  `nexus/operator_features/instances/`, until PR-G2 ships.
- After a definition change to an existing instance (idempotent MERGE
  — re-running with no changes is a no-op).

## Pre-flight

Per CANONICAL.md, Neptune mutations require explicit verbal Ian
confirmation. The script itself is read-only on first invocation
(`--dry-run`); the `write_operator_feature` call requires the gating.

```
ARIA_GRAPH_BACKEND=local python scripts/bootstrap_operator_features.py --dry-run
```

This validates that all instance modules import cleanly and exposes
the FEATURE constants the script will write. No Neptune reach.

## Production bootstrap

The script must run from a VPC-connected runtime (the workstation
cannot reach Neptune Analytics' private endpoint). Two equivalent
options:

### Option A — one-off ECS task on aria-console:64

```
aws ecs run-task \
  --cluster overwatch-platform \
  --task-definition aria-console:64 \
  --launch-type FARGATE \
  --network-configuration <ecs sg + private subnets> \
  --overrides '{"containerOverrides":[{"name":"aria-console",
                 "command":["python","-u",
                   "scripts/bootstrap_operator_features.py"]}]}'
```

### Option B — exec into a running aria-console task

```
aws ecs execute-command \
  --cluster overwatch-platform \
  --task <running task arn> \
  --container aria-console \
  --interactive \
  --command "python -u scripts/bootstrap_operator_features.py"
```

(Option A is preferred — no risk of sharing CPU/memory with live
traffic, and the task logs to its own CloudWatch stream.)

## Verification

After the bootstrap completes, confirm a Neptune node exists:

1. From any Echo-using surface, call
   `read_holograph(feature_id="ontology_capture_loop")`. The response
   should now include populated `health_signals` and `evidence_queries`,
   not the engine's "not found" stub.
2. Or directly query Neptune:
   ```
   MATCH (n:OperatorFeature {feature_id: 'ontology_capture_loop'})
   RETURN n.feature_id, n.name, n.tier, n.version_id
   ```

## Failure modes + recovery

- **Script exits non-zero with `No features discovered`**: PR-G1 has
  shipped but the instance modules have been removed or renamed.
  Investigate why discovery returned empty; verify
  `nexus/operator_features/instances/` contains files exposing
  `FEATURE` constants.
- **Script writes some features and fails on others**: per-feature
  failures are logged; the script exits with code 2. Re-run after
  fixing the failing feature (idempotent, completed writes are no-ops).
- **`read_holograph` still returns the stub after bootstrap**:
  verify the script ran in `ARIA_GRAPH_BACKEND=neptune` (not
  `local`); local mode writes to an in-memory store that doesn't
  persist across processes.

## Refs

- PR-G1: this script + runbook
- PR-G2: structural startup hook (planned follow-up)
- PR #57 / Phase 0e.4: shipped `ontology_capture_loop` definition
- Production rendering 2026-04-28T15:20Z: surfaced the gap that
  motivated PR-G1
- `nexus/operator_features/persistence.py:write_operator_feature`:
  the canonical write path the script delegates to
