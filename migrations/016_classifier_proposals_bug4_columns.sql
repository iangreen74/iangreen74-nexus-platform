-- Migration 016: classifier_proposals — Bug 4 rigorous fix columns
-- ===========================================================================
-- Adds 7 nullable columns required to support per-type schema-extractor
-- alignment for Decision and Hypothesis ontology objects. Substrate read
-- 2026-04-27 (`/tmp/bug4_rigorous_findings.md`) showed the classifier
-- extracts {title, summary, reasoning, confidence, context} regardless of
-- object_type while ontology schema requires:
--
--   Decision   ("name", "context", "choice_made", "reasoning",
--               "decided_at", "decided_by")
--   Hypothesis ("statement", "why_believed", "how_will_be_tested")
--   Feature    ("name", "description", "project_id")  ← already covered
--
-- Today's failure mode: Decision Accept fails 400
-- "Decision.choice_made is required" because the writer cannot ship a
-- field the classifier never captures. Hypothesis Accept fails analogously
-- on "Hypothesis.statement is required" — Hypothesis has been completely
-- broken since the schema was written.
--
-- Decision-required new columns:
--   choice_made             TEXT
--   decided_at              TIMESTAMPTZ
--   decided_by              TEXT
--   alternatives_considered TEXT  (Decision-optional but worth capturing
--                                  while we're touching the prompt)
--
-- Hypothesis-required new columns:
--   statement          TEXT
--   why_believed       TEXT
--   how_will_be_tested TEXT
--
-- Additive only. All NULLABLE. No existing data migration. Pre-migration
-- pending rows continue to ship (or fail to Accept) as before; new rows
-- created post-classifier-prompt-rewrite (PR-B) populate the new columns.
--
-- decided_at chosen as TIMESTAMPTZ (not TEXT) because the schema field is
-- semantically a timestamp and downstream consumers (Neptune writer,
-- ontology graph) will benefit from native typing. The classifier
-- extractor will emit ISO-8601 strings; psycopg2 coerces those to
-- TIMESTAMPTZ on INSERT.
--
-- alternatives_considered stored as TEXT (free-form list serialized by
-- the classifier) rather than JSONB. The Decision schema field is
-- ``List[str]`` (alternatives_considered: List[str] = field(default_factory=list))
-- but the writer can split on a delimiter at payload-build time — we keep
-- the column simple here, defer JSONB to a future migration if a real
-- need surfaces. Same call as ``raw_candidate JSONB`` vs ``edits JSONB``
-- elsewhere in this table — JSONB only when actually queried structurally.
--
-- Migration applied via the V1 runner introduced in PR #50:
--   python -m nexus.operator.db_apply_migration \
--     migrations/016_classifier_proposals_bug4_columns.sql --target=v1
--
-- This is the first migration to ship after PR #50's V1 substrate landed
-- in :latest. Migration 014 was applied via psql one-off because the
-- V1-aware runner code wasn't yet deployed at apply time; 016 is the
-- first real V1 runner exercise.
--
-- Refs:
-- - /tmp/bug4_rigorous_findings.md — substrate read for the rigorous fix
-- - docs/v1_migration_substrate_findings.md — V1 substrate context
-- - docs/v1_migration_014_apply_runbook.md — bootstrap precedent
-- ===========================================================================

ALTER TABLE classifier_proposals
  ADD COLUMN IF NOT EXISTS choice_made             TEXT,
  ADD COLUMN IF NOT EXISTS decided_at              TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS decided_by              TEXT,
  ADD COLUMN IF NOT EXISTS alternatives_considered TEXT,
  ADD COLUMN IF NOT EXISTS statement               TEXT,
  ADD COLUMN IF NOT EXISTS why_believed            TEXT,
  ADD COLUMN IF NOT EXISTS how_will_be_tested      TEXT;
