-- Mechanism 3: Socratic proactive prompts.
-- Sprint 13 Day 3.

CREATE TABLE IF NOT EXISTS socratic_prompts (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    rule_name TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_id TEXT,
    question TEXT NOT NULL,
    rationale TEXT,
    priority INT DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    surfaced_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    UNIQUE (tenant_id, rule_name, subject_id)
);

CREATE INDEX IF NOT EXISTS socratic_prompts_tenant_pending_idx
    ON socratic_prompts (tenant_id, status, priority DESC)
    WHERE status IN ('pending', 'surfaced');

CREATE INDEX IF NOT EXISTS socratic_prompts_created_idx
    ON socratic_prompts (created_at DESC);
