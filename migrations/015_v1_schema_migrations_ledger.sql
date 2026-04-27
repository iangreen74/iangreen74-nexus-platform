-- Migration 015: V1 schema_migrations ledger (bootstrap)
-- ===========================================================================
-- Creates the V1 schema_migrations ledger so future V1 migrations can be
-- applied through `nexus.operator.db_apply_migration --target=v1` and
-- recorded for drift detection.
--
-- Mirrors the V2 ledger shape exactly:
--   schema_migrations(filename TEXT PRIMARY KEY,
--                     applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
--                     checksum TEXT NOT NULL)
--
-- The two ledgers are SEPARATE TABLES in SEPARATE Postgres instances —
-- applying a migration only marks it in the targeted ledger. See
-- docs/v1_migration_substrate_findings.md for the V1/V2 split.
--
-- BOOTSTRAP NOTE: this migration cannot itself be applied via the runner
-- (the runner expects the ledger to exist in order to record the apply).
-- It must be applied via psql one-off the first time. After this lands,
-- all future V1 migrations apply through the runner normally.
--
-- BACKFILL: migration 012 was applied to V1 ~2026-04-26 *before any
-- runner existed* (raw psql one-off, see docs/SPRINT_14_DAY_2_HANDOVER.md
-- "Save 5"). Its sha256 against the file in this repo is recorded below
-- so re-running the runner against 012 reports "already_applied_matching"
-- rather than re-executing or flagging drift.
--
-- Migration 014 is NOT pre-recorded here — it gets its row when commit 5
-- of this PR actually applies it through the runner.
--
-- Refs: docs/v1_migration_substrate_findings.md
-- ===========================================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
);

-- Backfill migration 012 (applied pre-runner-existence).
-- sha256 computed against the file in this repo at the time of this
-- migration's authorship; see commit message for the canonical value.
INSERT INTO schema_migrations (filename, checksum) VALUES
  ('012_classifier_proposals_source_kind.sql',
   '8fb137905cd0341bf68a90ed5322d468669aa7edd719207de415c8764c06e290')
ON CONFLICT (filename) DO NOTHING;
