-- Migration 012: add source_kind to classifier_proposals
-- ===========================================================================
-- source_kind: TYPE of producer that created this proposal.
--
-- Reserved enum values:
--   conversation_classifier  - mechanism1 (live as of 2026-04-26)
--   deploy_event_classifier  - mechanism2 (unbuilt as of 2026-04-26 —
--                              nexus/mechanism2/ is an empty directory; the
--                              `_deploy_failure_streak` Socratic rule in
--                              nexus/mechanism3/rules.py reads for these rows
--                              but no producer writes them yet)
--   socratic_scheduler       - mechanism3 (rule layer; would attribute
--                              proposals it generates if/when extended to
--                              write rather than only read)
--   manual                   - operator-created
--
-- Distinct from `proposed_via` (used by nexus/mechanism1/proposals.py:188 in
-- the eval-corpus ActionEvent write): source_kind is the *kind* of producer
-- (one-of the enum above); proposed_via is the *implementation tag* —
-- free-form, versioned, e.g. "classifier_m1", "classifier_m2_v3". The two
-- co-exist: source_kind is the kind, proposed_via is the version/instance.
--
-- Additive only. Default NULL. Existing rows are unattributable
-- (mechanism1 has been the only writer, all "conversation_turn" semantically,
-- but back-filling would assert a fact we can no longer verify per-row).
--
-- Migration applied manually via one-off ECS task with psql against the
-- VPC-internal RDS instance (no automated runner).
-- ===========================================================================

ALTER TABLE classifier_proposals
  ADD COLUMN IF NOT EXISTS source_kind TEXT;

CREATE INDEX IF NOT EXISTS idx_classifier_proposals_source_kind
  ON classifier_proposals (tenant_id, source_kind)
  WHERE source_kind IS NOT NULL;
