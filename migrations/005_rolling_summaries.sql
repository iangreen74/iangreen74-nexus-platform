-- Phase 6: Rolling summaries for ARIA memory compression.
-- Daily/weekly/monthly summaries per tenant.
-- Sprint 13 Day 3.

CREATE TABLE IF NOT EXISTS rolling_summaries (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    horizon TEXT NOT NULL,
    for_date DATE NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, horizon, for_date)
);

CREATE INDEX IF NOT EXISTS idx_rolling_summaries_tenant_horizon
    ON rolling_summaries(tenant_id, horizon, for_date DESC);
