-- Migration 001: ontology_object_versions
-- Sprint 13 Day 1 B5-prov. Apply once after RDS stack deploys.
-- Apply with: psql "$DATABASE_URL" -f migrations/001_ontology_object_versions.sql

CREATE TABLE IF NOT EXISTS ontology_object_versions (
    id                          BIGSERIAL PRIMARY KEY,
    version_id                  UUID NOT NULL UNIQUE,
    ontology_id                 TEXT NOT NULL,
    tenant_id                   TEXT NOT NULL,
    project_id                  TEXT,
    object_type                 TEXT NOT NULL
                                  CHECK (object_type IN ('feature','decision','hypothesis')),
    object_data                 JSONB NOT NULL,
    proposed_via                TEXT NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_by_version_id    UUID NULL REFERENCES ontology_object_versions(version_id)
);

CREATE INDEX IF NOT EXISTS idx_ontology_versions_tenant
    ON ontology_object_versions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ontology_versions_ontology_id
    ON ontology_object_versions(ontology_id);
CREATE INDEX IF NOT EXISTS idx_ontology_versions_created_at
    ON ontology_object_versions(created_at DESC);
