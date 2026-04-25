-- 007_engineering_object_versions.sql
-- V2 ontology: versioned engineering objects.
-- Mirrors the migration 001 (ontology_object_versions) pattern.
-- Postgres holds canonical version history; Neptune holds queryable graph projection.

CREATE TABLE IF NOT EXISTS engineering_object_versions (
    object_id      TEXT NOT NULL,
    version_id     INTEGER NOT NULL,
    object_type    TEXT NOT NULL,
    properties     JSONB NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_from     TIMESTAMPTZ NOT NULL,
    valid_to       TIMESTAMPTZ,
    created_by     TEXT NOT NULL,
    PRIMARY KEY (object_id, version_id)
);

CREATE INDEX IF NOT EXISTS idx_eov_current
    ON engineering_object_versions (object_id) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_eov_type
    ON engineering_object_versions (object_type);
CREATE INDEX IF NOT EXISTS idx_eov_created_by
    ON engineering_object_versions (created_by);
