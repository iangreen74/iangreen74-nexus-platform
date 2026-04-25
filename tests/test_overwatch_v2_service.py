"""Service-layer tests for the V2 ontology.

NEXUS_MODE=local routes writes to local_store. Each test resets state.
"""
import os
os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from nexus.overwatch_v2.ontology import (  # noqa: E402
    propose_object, update_object, create_link, get_object,
    list_objects_by_type, query,
    V2ObjectNotFoundError, V2SchemaValidationError, V2EdgeValidationError,
)
from nexus.overwatch_v2.ontology import local_store  # noqa: E402


def _reset():
    local_store.reset()


# --- propose_object happy paths --------------------------------------------

def test_propose_engineering_task_returns_ids():
    _reset()
    r = propose_object("EngineeringTask",
                      {"title": "fix verify-health", "description": "d"})
    assert r["object_id"]
    assert r["version_id"] == 1
    assert r["action_event_id"]


def test_propose_investigation():
    _reset()
    r = propose_object("Investigation",
                      {"hypothesis": "ALB limit", "methodology": "describe-events"})
    assert r["version_id"] == 1


def test_propose_hypothesis():
    _reset()
    r = propose_object("Hypothesis", {"claim": "subnet IPs exhausted"})
    assert r["object_id"]


def test_propose_decision():
    _reset()
    r = propose_object("Decision",
                      {"question": "deploy?", "chosen": "yes",
                       "rationale": "tests pass"})
    assert r["version_id"] == 1


def test_propose_fix_attempt():
    _reset()
    r = propose_object("FixAttempt",
                      {"task_id": "task-1", "description": "patch policy"})
    assert r["object_id"]


def test_propose_assigns_unique_object_ids():
    _reset()
    a = propose_object("Hypothesis", {"claim": "x"})["object_id"]
    b = propose_object("Hypothesis", {"claim": "y"})["object_id"]
    assert a != b


def test_propose_records_to_local_store():
    _reset()
    propose_object("EngineeringTask", {"title": "t", "description": "d"})
    assert len(local_store.list_by_type("EngineeringTask")) == 1


def test_propose_unknown_type_raises():
    _reset()
    with pytest.raises(V2SchemaValidationError):
        propose_object("NotAType", {"title": "t"})


def test_propose_validation_failure_does_not_write():
    _reset()
    with pytest.raises(V2SchemaValidationError):
        propose_object("EngineeringTask", {"title": ""})  # missing description
    assert local_store.list_by_type("EngineeringTask") == []


# --- update_object behaviour -----------------------------------------------

def test_update_increments_version():
    _reset()
    a = propose_object("EngineeringTask", {"title": "t", "description": "d"})
    u = update_object(a["object_id"], {"status": "in_progress"})
    assert u["version_id"] == 2


def test_update_supersedes_prior_version():
    _reset()
    a = propose_object("EngineeringTask", {"title": "t", "description": "d"})
    update_object(a["object_id"], {"status": "completed"})
    versions = local_store.list_versions_for(a["object_id"])
    assert len(versions) == 2
    v1 = next(v for v in versions if v["version_id"] == 1)
    v2 = next(v for v in versions if v["version_id"] == 2)
    assert v1.get("valid_to") is not None
    assert v2.get("valid_to") is None


def test_update_unknown_object_raises():
    _reset()
    with pytest.raises(V2ObjectNotFoundError):
        update_object("nonexistent-id", {"status": "completed"})


def test_update_invalid_property_raises():
    _reset()
    a = propose_object("EngineeringTask", {"title": "t", "description": "d"})
    with pytest.raises(V2SchemaValidationError):
        update_object(a["object_id"], {"status": "bogus"})


def test_update_preserves_unset_fields():
    _reset()
    a = propose_object("EngineeringTask",
                       {"title": "t", "description": "orig"})
    update_object(a["object_id"], {"status": "in_progress"})
    cur = get_object(a["object_id"])
    # description stays from v1
    assert cur["description"] == "orig"


# --- get_object ------------------------------------------------------------

def test_get_object_returns_current_when_no_version_arg():
    _reset()
    a = propose_object("Hypothesis", {"claim": "c"})
    cur = get_object(a["object_id"])
    assert cur is not None
    assert cur["version_id"] == 1


def test_get_object_returns_specific_version():
    _reset()
    a = propose_object("Hypothesis", {"claim": "c"})
    update_object(a["object_id"], {"claim": "c2"})
    v1 = get_object(a["object_id"], version=1)
    assert v1 is not None
    assert v1["claim"] == "c"


def test_get_object_unknown_returns_none():
    _reset()
    assert get_object("does-not-exist") is None


# --- create_link -----------------------------------------------------------

def test_create_link_valid_pair():
    _reset()
    t = propose_object("EngineeringTask",
                      {"title": "t", "description": "d"})["object_id"]
    i = propose_object("Investigation",
                      {"hypothesis": "h", "methodology": "m"})["object_id"]
    r = create_link(t, i, "INVESTIGATES")
    assert r["edge_id"]


def test_create_link_records_edge_in_local_store():
    _reset()
    t = propose_object("EngineeringTask",
                      {"title": "t", "description": "d"})["object_id"]
    i = propose_object("Investigation",
                      {"hypothesis": "h", "methodology": "m"})["object_id"]
    create_link(t, i, "INVESTIGATES")
    edges = local_store.list_edges()
    assert len(edges) == 1
    assert edges[0]["edge_type"] == "INVESTIGATES"
    assert edges[0]["from_id"] == t
    assert edges[0]["to_id"] == i


def test_create_link_wrong_source_type_raises():
    _reset()
    f = propose_object("Failure", {"what": "boom"})["object_id"]
    h = propose_object("Hypothesis", {"claim": "c"})["object_id"]
    with pytest.raises(V2EdgeValidationError):
        create_link(f, h, "INVESTIGATES")


def test_create_link_unknown_from_id_raises():
    _reset()
    with pytest.raises(V2ObjectNotFoundError):
        create_link("nope", "also-nope", "INVESTIGATES")


def test_create_link_freeform_target_accepts_unknown_id():
    _reset()
    f = propose_object("Failure", {"what": "boom"})["object_id"]
    # CAUSED_BY allows any target ID (resource_id, not a node)
    r = create_link(f, "arn:aws:ecs:us-east-1:foo", "CAUSED_BY")
    assert r["edge_id"]


# --- list_objects_by_type --------------------------------------------------

def test_list_objects_by_type_returns_only_current_versions():
    _reset()
    a = propose_object("Hypothesis", {"claim": "c1"})
    propose_object("Hypothesis", {"claim": "c2"})
    update_object(a["object_id"], {"claim": "c1-updated"})
    rows = list_objects_by_type("Hypothesis")
    assert len(rows) == 2
    claims = [r["claim"] for r in rows]
    assert "c1" not in claims
    assert "c1-updated" in claims
    assert "c2" in claims


def test_list_objects_by_type_filters_by_label():
    _reset()
    propose_object("Hypothesis", {"claim": "c"})
    propose_object("Decision",
                   {"question": "q?", "chosen": "a", "rationale": "r"})
    assert len(list_objects_by_type("Hypothesis")) == 1
    assert len(list_objects_by_type("Decision")) == 1


def test_list_objects_by_type_unknown_raises():
    _reset()
    with pytest.raises(V2SchemaValidationError):
        list_objects_by_type("NotAType")


def test_list_objects_by_type_respects_limit():
    _reset()
    for i in range(5):
        propose_object("Hypothesis", {"claim": f"c{i}"})
    rows = list_objects_by_type("Hypothesis", limit=3)
    assert len(rows) == 3


# --- query ------------------------------------------------------------------

def test_query_returns_empty_in_local_mode():
    _reset()
    # Local mode short-circuits to []
    assert query("MATCH (n) RETURN n") == []


# --- production-mode error propagation -------------------------------------

def test_production_postgres_failure_does_not_swallow():
    """Postgres-first: if Postgres errors, the exception surfaces (no Neptune write)."""
    from nexus.overwatch_v2.ontology import service as svc
    with patch.object(svc, "_is_production", return_value=True), \
         patch("nexus.overwatch_v2.ontology.service.postgres.insert_version",
               side_effect=RuntimeError("PG down")), \
         patch("nexus.overwatch_v2.ontology.service.graph.merge_object") as gm:
        with pytest.raises(RuntimeError, match="PG down"):
            propose_object("EngineeringTask",
                           {"title": "t", "description": "d"})
        gm.assert_not_called()


def test_production_neptune_failure_after_pg_success_surfaces():
    """Neptune fails after Postgres succeeded — sequential, NOT transactional.
    Postgres write is NOT rolled back. Document this explicitly."""
    from nexus.overwatch_v2.ontology import service as svc
    with patch.object(svc, "_is_production", return_value=True), \
         patch("nexus.overwatch_v2.ontology.service.postgres.insert_version") as pg, \
         patch("nexus.overwatch_v2.ontology.service.graph.merge_object",
               side_effect=RuntimeError("Neptune down")):
        with pytest.raises(RuntimeError, match="Neptune down"):
            propose_object("EngineeringTask",
                           {"title": "t", "description": "d"})
        pg.assert_called_once()


# --- reconciliation idempotency (local) -------------------------------------

def test_reset_clears_local_store():
    _reset()
    propose_object("EngineeringTask", {"title": "t", "description": "d"})
    _reset()
    assert local_store.list_by_type("EngineeringTask") == []
    assert local_store.list_edges() == []
