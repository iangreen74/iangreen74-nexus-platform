# Migration 014 apply runbook (Sprint 15 Day 3, 2026-04-27)

## Summary

Migrations 015 (V1 schema_migrations ledger) and 014
(`classifier_proposals.context`) applied to V1 Postgres
(`nexus-ontology-postgres`) on 2026-04-27 via psql one-off through
`aria-console:64` task def. Bootstrap-style apply because the new
V1-aware runner (commit 2) and the V1 task def (commit 4) ship in the
same PR — neither was deployed yet at apply time.

## Why bootstrap rather than runner

The V1-aware runner code (`db_apply_migration` with `--target=v1`)
landed in commit 2 but the running ECR image (`nexus-platform:latest`)
was built from `main` before this branch existed. Auto-deploy fires on
merge to `main`, not branch push. So at apply time the live image had
the V2-only runner, not the V1-aware one.

Two paths considered:

- **Build/push new image, deploy V1 task def CFN, run via new runner.**
  Adds two extra production touches (image push + CFN deploy) before
  the migration apply itself.
- **psql one-off via existing `aria-console:64` task def.** That task
  def already has `DATABASE_URL` set and the image carries
  `postgresql-client` (Dockerfile line 16-17, "one-off ECS tasks apply
  migrations from inside the VPC"). Single production action per step.

Picked the second. Substrate (commits 1-4) ships for future V1
migrations to use; this one was bootstrapped.

## Reachability verification (pre-apply)

Confirmed before any `run-task`:

- V1 RDS `nexus-ontology-postgres`: VPC `vpc-048ee32658b49e0a0`,
  AZ us-east-1a, primary SG `sg-0dc6f71da7d0db4bc`.
- V1 RDS SG inbound rule: TCP/5432 from `sg-075797efad9ec368d` (the
  ECS SG used by the runner subnets).
- Runner subnets `subnet-0636e59cf68ff3844` (us-east-1b) and
  `subnet-0797dba8499525215` (us-east-1a) — same VPC as RDS, AZ-a
  overlap with the active instance.

## Apply sequence

Five sequential `aws ecs run-task` invocations on cluster
`overwatch-platform`, each with a small idempotent `command` override
on `aria-console:64`.

| Step | Task ARN suffix | Exit | Output (key line) |
|---|---|---|---|
| 1. Pre-verify column missing | `7fa13cef58464b0583d30922a039bf62` | 0 | `COLUMN_MISSING_PROCEED_OK` |
| 2. Apply 015 (CREATE TABLE + backfill 012) | `c1ab1c6fc157498d98de8ca5087c2092` | 0 | `CREATE TABLE` / `INSERT 0 1` |
| 3. Apply 014 (ALTER TABLE) | `b6524f331e634c11be3f6b6bb86caf36` | 0 | `ALTER TABLE` |
| 4. Record 014 in ledger | `04eb2e38502e498d8deccc8358edf957` | 0 | `INSERT 0 1` |
| 5. Post-verify column present | `3acedcc282854f3192d8726a7a96d6a8` | 0 | `context\|text\|YES` (nullable) |

Network config used for all five:

```
awsvpcConfiguration={subnets=[subnet-0636e59cf68ff3844,subnet-0797dba8499525215],securityGroups=[sg-075797efad9ec368d],assignPublicIp=DISABLED}
```

Logs streamed to `/aria/console`, stream name
`console/aria-console/<task-id>`.

## Final V1 ledger state (post-apply)

```
012_classifier_proposals_source_kind.sql | 8fb137905cd0 | 2026-04-27 18:13:24.381996+00
014_classifier_proposals_context.sql     | 24b99abe20b3 | 2026-04-27 18:16:13.194076+00
```

Checksums match the file content in this repo at commit 3 / commit 1
respectively. Re-running migration 015 or 014 through the new V1
runner (post-deploy) will report `already_applied_matching` and not
re-execute.

## Smoke verification

Production CloudWatch `/ecs/forgescaler` log group, filter pattern
`"column \"context\" does not exist"`:

| Window | Count |
|---|---|
| 18:18:20 UTC → 18:30:00 UTC (12 min after apply) | 0 |
| 18:25 UTC → 18:35 UTC (latest 10 min at runbook-write time) | 0 |

Pre-migration error rate had been ~25 events / 90 min sampled earlier
this morning. Post-migration: zero, both in the immediate window after
apply and continuing into the present.

## What still might fail

- **Pre-migration stale Decision proposals** with `context = NULL`
  remain pending. Aria-platform writer defensively omits null context
  from outgoing payload — but the ontology service requires `context`
  for Decision objects. So those stay pending until they expire or are
  replaced. Separate UX problem ("orphan stale Decisions"), out of
  scope for this PR.
- **Future V1 migrations** can now use the new runner with
  `--target=v1` after `:latest` updates on merge. The next V1
  migration to ship will be the first real test of the new runner +
  task def in production.

## Refs

- `docs/v1_migration_substrate_findings.md` (commit 1)
- `migrations/014_classifier_proposals_context.sql` (PR #48)
- `migrations/015_v1_schema_migrations_ledger.sql` (commit 3)
- `nexus/operator/db_apply_migration.py` V1 path (commit 2)
- `infra/migration-apply-task-v1.yml` (commit 4)
