-- 008_proposals.sql
-- V2 approval-gated proposals for write-class agent capabilities.

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id        UUID PRIMARY KEY,
    capability_name    TEXT NOT NULL,
    payload            JSONB NOT NULL,
    rationale          TEXT NOT NULL,
    risk_level         TEXT NOT NULL,
    rollback_plan      TEXT NOT NULL,
    affected_systems   JSONB NOT NULL,
    state              TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at         TIMESTAMPTZ NOT NULL,
    approval_token_id  UUID,
    execution_result   JSONB
);

CREATE INDEX IF NOT EXISTS idx_proposals_state ON proposals (state);
CREATE INDEX IF NOT EXISTS idx_proposals_capability ON proposals (capability_name);
CREATE INDEX IF NOT EXISTS idx_proposals_created ON proposals (created_at DESC);
