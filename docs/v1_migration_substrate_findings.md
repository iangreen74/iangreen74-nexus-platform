# V1 migration substrate findings — Sprint 15 Day 3 (2026-04-27)

## Why this doc exists

PR #48 (merged 2026-04-27 ~09:21 UTC) shipped
`migrations/014_classifier_proposals_context.sql` expecting it would
auto-apply on deploy. PR #84 (aria-platform, merged ~09:36 UTC) then
shipped writer.py changes that `SELECT context FROM classifier_proposals`.
Track A diagnostic confirmed every classifier Accept since has returned
HTTP 404 with `column "context" does not exist`.

Pre-write diagnosis surfaced the cause: the migration runner in this
repo is **V2-only**. PR #48's migration file landed in a repo whose
infrastructure could not apply it.

This doc captures the pre-write substrate findings before any code
change. Subsequent commits in PR `feat/v1-migration-substrate` add the
V1-targeted runner, task def, ledger, and apply migration 014 through
the new path.

## The split

VaultScaler runs **two** Postgres instances:

| | V1 — `nexus-ontology-postgres` | V2 — `overwatch-postgres` |
|---|---|---|
| Instance class | `db.t4g.micro` | `db.t4g.medium` |
| AZ | `us-east-1a` | `us-east-1b` |
| Connection secret | `nexus/ontology/postgres/connection-XlBoLD` | `overwatch-v2/postgres-master-e16PNJ` |
| Secret format | JSON `{ "DATABASE_URL": "postgres://..." }` | JSON `{ "host", "port", "username", "password", "dbname" }` |
| Env-var convention | `DATABASE_URL` | `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DBNAME` |
| Python connector | `nexus/ontology/postgres.py:_connect()` (V1) and `nexus/mechanism1/proposals.py:_pg_connect()` (V1) | `nexus/overwatch_v2/db.py:get_conn()` (V2) |
| Migration runner | **none documented** | `nexus.operator.db_apply_migration` |
| Migration task def | **none documented** | `aria-console-migration-apply` (`infra/overwatch-v2/18-migration-apply-task.yml`) |
| Schema ledger | **does not exist** | `schema_migrations(filename, applied_at, checksum)` |

Tables live in whichever DB their connector wired them to. V1 carries
`classifier_proposals`, `classifier_proposals_source_kind`, ontology
object versions, and the data founder Decisions/Features/Hypotheses
flow through. V2 carries `approval_tokens`, the operator-features
substrate (Phase 0e.1), and the Overwatch operational state.

## Why migration 012 silently worked

Migration `012_classifier_proposals_source_kind.sql` was applied
2026-04-26-ish, before the V2 runner existed. The Phase 1.5 handover
(`docs/SPRINT_14_DAY_2_HANDOVER.md`, "Save 5") says:

> No automated migration runner exists. The Dockerfile installs
> `postgresql-client` for "one-off ECS tasks apply migrations from
> inside the VPC."

So 012 was applied via raw `psql` in a one-off ECS task. There was no
ledger to record the apply; subsequent verification was eyeball-only.

Phase 1.5 then built `nexus.operator.db_apply_migration` — but **wired
to V2** (the next migration to ship was 013, V2's
`approval_tokens_align_with_code`). The V1 case was left implicit: the
runner targeted V2 because that was the immediate need. Nobody wrote
"V1 still uses raw psql" anywhere. The infrastructure now looks
universal but only the V2 path actually works.

## The hidden gap

When PR #48 shipped migration 014 (V1, `classifier_proposals.context`),
nothing surfaced "this runner can't apply it." The author and reviewer
both implicitly assumed migrations were a single discipline. The PR
landed; the column did not.

CI did not catch it:
- `pytest tests/test_operator_db_apply_migration.py` passes — runner
  code is unchanged
- `pytest tests/test_mechanism1.py` passes — proposals.py still works
  in local mode against an in-memory dict
- No integration test exercises the production V1 RDS path, because
  dev machines can't reach it (RDS SG blocks)

The gap was substrate-shaped, not code-shaped. The fix is also
substrate-shaped.

## Decision tree: which path applies for a new migration?

```
Does the migration touch a V1 table (classifier_proposals, ontology
objects, founder data)?
├── Yes → V1 runner. After this PR:
│     aws ecs run-task --task-definition aria-console-migration-apply-v1 \
│       --cluster overwatch-platform --launch-type FARGATE \
│       --overrides 'command=["python", "-m",
│         "nexus.operator.db_apply_migration",
│         "migrations/0NN_*.sql", "--target=v1"]'
└── No → V2 runner (existing infrastructure):
      aws ecs run-task --task-definition aria-console-migration-apply \
        --cluster overwatch-platform --launch-type FARGATE
```

The runner accepts `--target` explicitly (no default after this PR);
the matching task def already wires the right secrets.

## Bootstrap concern

The V1 ledger (migration 015 in this PR) cannot itself be applied via
the runner — the runner expects the ledger to exist in order to record
the apply. Solution: 015 is applied via raw psql one-off the first
time, then every subsequent V1 migration uses the runner.

Migration 014 is a **second bootstrap-adjacent case**: at the time of
this PR the new image carrying the V1-aware runner is not yet on
`:latest` (auto-deploy fires on merge to main, not branch). For *this
one apply*, both 015 and 014 are sequenced through psql one-off in the
same ECS task. After this PR ships, the next V1 migration will be the
first to actually use the runner.

This pragmatic two-step is documented explicitly in commit 5 so it
isn't mistaken for "the runner doesn't work" by future readers.

## Side observation: GITHUB_TOKEN exposure

While reading the `aria-console:64` task definition for substrate
diagnosis, I noticed `HYPERLEV_GITHUB_TOKEN` and `GITHUB_TOKEN` set as
**plaintext environment values** rather than Secrets Manager
references. This is a separate cleanup outside the scope of this PR
but worth flagging — task-def env values are visible to anyone with
`ecs:DescribeTaskDefinition`, which is a wider audience than
`secretsmanager:GetSecretValue`.

## Refs

- `/tmp/bug4_post_merge_diag.md` — Track A 422/404 diagnostic
- PR #48 — migration 014 ship without apply
- PR #84 (aria-platform) — writer.py SELECTing the missing column
- `docs/SPRINT_14_DAY_2_HANDOVER.md` — Phase 1.5.1 V2 runner build,
  "Save 5" the manual-runner gap entry
- `nexus/operator/db_apply_migration.py` — V2 runner
- `infra/overwatch-v2/18-migration-apply-task.yml` — V2 task def CFN
