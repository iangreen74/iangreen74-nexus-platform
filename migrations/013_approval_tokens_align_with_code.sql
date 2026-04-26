-- 013_approval_tokens_align_with_code.sql
--
-- Phase 1.5 — close the production token-ledger gap surfaced after PR #39
-- (Phase 1) shipped. Migration 010 created an approval_tokens table whose
-- shape diverged from what nexus/overwatch_v2/auth/approval_tokens.py
-- actually INSERTs / UPDATEs at runtime:
--
--   Migration 010 schema  : (token_id UUID PK, proposal_id UUID NOT NULL
--                            FK -> proposals(proposal_id), issued_at,
--                            expires_at, used, used_at)
--   Code's INSERT shape   : (token_id, proposal_id, proposal_hash, issued_at,
--                            expires_at, issuer, used)
--
-- Two reconciliations:
--
-- (a) Add the two missing columns: proposal_hash, issuer.
--
-- (b) Relax proposal_id from UUID FK to TEXT.
--
--     proposal_id is TEXT going forward; it MAY match a proposals.proposal_id
--     for multi-step capability proposals (propose_commit -> execute_commit),
--     OR it MAY be a synthesized "tool:<name>" identifier for one-shot
--     mutations like comment_on_pr. The contract is "proposal-like entity
--     reference," NOT "always references a proposals row." Per V2 SPECIFICATION
--     §5.4 and the gate's docstring in nexus/overwatch_v2/tools/_approval_gate.py,
--     both forms are valid; the FK on migration 010 was over-constrained.
--
-- Migration 010 is intentionally NOT modified — migrations are append-only.
-- Numbering note: 011 is agent_conversation_turns, 012 is the source_kind
-- column on classifier_proposals; 013 is the next free number, NOT 011.
--
-- Apply via: python -m nexus.operator.db_apply_migration migrations/013_approval_tokens_align_with_code.sql
-- (Operator runs from inside the VPC via ECS exec session — RDS SG only
-- allows ingress from inside the VPC.)

-- (b) Drop the FK first (no-op if migration 010 was never applied).
ALTER TABLE approval_tokens
    DROP CONSTRAINT IF EXISTS approval_tokens_proposal_id_fkey;

-- Convert proposal_id from UUID to TEXT.
ALTER TABLE approval_tokens
    ALTER COLUMN proposal_id TYPE TEXT USING proposal_id::TEXT;

-- (a) Add the missing columns. NULLable for now so the migration tolerates
-- pre-existing rows (if any); a future tightening migration can flip these
-- to NOT NULL after the backfill below settles.
ALTER TABLE approval_tokens ADD COLUMN IF NOT EXISTS proposal_hash TEXT;
ALTER TABLE approval_tokens ADD COLUMN IF NOT EXISTS issuer        TEXT;

-- Backfill any pre-013 rows so future NOT NULL tightening doesn't trip
-- on legacy data. Sentinel values are deliberately ugly so they're easy
-- to grep for if anyone wants to audit which rows pre-date Phase 1.5.
UPDATE approval_tokens SET proposal_hash = 'PRE_MIGRATION_013' WHERE proposal_hash IS NULL;
UPDATE approval_tokens SET issuer        = 'PRE_MIGRATION_013' WHERE issuer        IS NULL;

-- The original idx_at_proposal index from migration 010 was on a UUID column;
-- after the type change to TEXT, Postgres rebuilds the index automatically
-- as part of the ALTER TYPE — no DROP/CREATE needed here.
