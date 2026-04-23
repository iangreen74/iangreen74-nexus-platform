-- Phase 5: Tone marker storage for ARIA emotional arc.
-- Each row is a ToneMarker from the tone classifier.
-- Sprint 13 Day 3.

CREATE TABLE IF NOT EXISTS tone_markers (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    turn_id TEXT,
    detail JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tone_markers_tenant_recent
    ON tone_markers(tenant_id, created_at DESC);
