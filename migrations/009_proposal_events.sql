-- 009_proposal_events.sql
-- V2 append-only audit trail of every proposal state transition.

CREATE TABLE IF NOT EXISTS proposal_events (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id     UUID NOT NULL REFERENCES proposals (proposal_id),
    event_type      TEXT NOT NULL,
    actor           TEXT NOT NULL,
    payload         JSONB,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pe_proposal ON proposal_events (proposal_id, occurred_at);

CREATE OR REPLACE FUNCTION reject_proposal_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'proposal_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS proposal_events_append_only ON proposal_events;
CREATE TRIGGER proposal_events_append_only
BEFORE UPDATE OR DELETE ON proposal_events
FOR EACH ROW EXECUTE FUNCTION reject_proposal_event_mutation();
