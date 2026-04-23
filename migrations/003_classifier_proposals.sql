-- Mechanism 1: Classifier proposal persistence.
-- Stores Haiku-extracted ontology candidates pending founder disposition.
-- Sprint 13 Day 2.

CREATE TABLE IF NOT EXISTS classifier_proposals (
    candidate_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    object_type TEXT NOT NULL CHECK (object_type IN ('feature', 'decision', 'hypothesis')),
    title TEXT NOT NULL,
    summary TEXT,
    reasoning TEXT,
    confidence NUMERIC(3,2),
    source_turn_id TEXT,
    raw_candidate JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'edited', 'rejected')),
    dispositioned_by TEXT,
    dispositioned_at TIMESTAMPTZ,
    edits JSONB,
    reject_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_classifier_proposals_tenant_pending
    ON classifier_proposals(tenant_id, status)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_classifier_proposals_created
    ON classifier_proposals(created_at DESC);
