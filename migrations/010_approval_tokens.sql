-- 010_approval_tokens.sql
-- V2 single-use, time-bounded JWT tokens authorising one approved capability execution.

CREATE TABLE IF NOT EXISTS approval_tokens (
    token_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id   UUID NOT NULL REFERENCES proposals (proposal_id),
    issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    used          BOOLEAN NOT NULL DEFAULT FALSE,
    used_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_at_proposal ON approval_tokens (proposal_id);
CREATE INDEX IF NOT EXISTS idx_at_unused ON approval_tokens (token_id) WHERE used = FALSE;
