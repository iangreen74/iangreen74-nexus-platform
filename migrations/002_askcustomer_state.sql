-- Migration 002: ask_customer_state
-- Sprint 13 Day 2 — AskCustomer primitive. Apply after 001.
-- psql "$DATABASE_URL" -f migrations/002_askcustomer_state.sql

CREATE TABLE IF NOT EXISTS ask_customer_state (
    proposal_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    question TEXT NOT NULL,
    options JSONB NOT NULL,
    context JSONB,
    task_token TEXT,
    state_machine_execution_arn TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending','answered','expired','cancelled')),
    answer JSONB,
    answered_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_askcustomer_tenant_pending
    ON ask_customer_state(tenant_id, status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_askcustomer_created
    ON ask_customer_state(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_askcustomer_expires
    ON ask_customer_state(expires_at) WHERE status = 'pending' AND expires_at IS NOT NULL;
