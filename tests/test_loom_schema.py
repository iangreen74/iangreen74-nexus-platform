"""Tests for Loom v0 schema, types, and validation."""
from __future__ import annotations

import json
import pytest

from nexus.ontology.exceptions import SchemaValidationError
from nexus.ontology.schema import Decision, Feature, Hypothesis, object_class_for
from nexus.ontology.types import (
    DecisionStatus, FeatureStatus, HypothesisStatus, LinkType, ObjectType, Visibility,
)

BASE = dict(
    id="obj-1", tenant_id="forge-t", version_id=1,
    created_at="2026-04-20T12:00:00+00:00", updated_at="2026-04-20T12:00:00+00:00",
    created_by="founder-1",
)


class TestEnums:
    def test_object_type_values(self):
        assert ObjectType.values() == {"Feature", "Decision", "Hypothesis"}

    def test_link_type_values(self):
        assert LinkType.values() == {"motivates", "supersedes", "validates"}

    def test_feature_status_values(self):
        assert "proposed" in FeatureStatus.values()
        assert "shipped" in FeatureStatus.values()

    def test_visibility_default(self):
        assert Visibility.WORKSPACE.value == "workspace"


class TestFeature:
    def _valid(self, **kw):
        d = dict(BASE, object_type="Feature", project_id="proj-p",
                 name="Login", description="Email+password login")
        d.update(kw)
        return Feature(**d)

    def test_valid(self):
        f = self._valid()
        assert f.status == FeatureStatus.PROPOSED.value

    def test_requires_name(self):
        with pytest.raises(SchemaValidationError, match="name"):
            self._valid(name="")

    def test_requires_description(self):
        with pytest.raises(SchemaValidationError, match="description"):
            self._valid(description="")

    def test_requires_project_id(self):
        with pytest.raises(SchemaValidationError, match="project_id"):
            Feature(**BASE, object_type="Feature", name="x", description="y")

    def test_invalid_status(self):
        with pytest.raises(SchemaValidationError, match="status"):
            self._valid(status="bogus")

    def test_wrong_object_type(self):
        with pytest.raises(SchemaValidationError, match="object_type"):
            Feature(**BASE, object_type="Decision", project_id="p", name="x", description="y")

    def test_neptune_props_strips_nones(self):
        props = self._valid().to_neptune_props()
        assert "shipped_at" not in props
        assert props["name"] == "Login"


class TestDecision:
    def _valid(self, **kw):
        d = dict(BASE, object_type="Decision", name="Use Neptune",
                 context="Need graph", alternatives_considered=["Neo4j", "DDB"],
                 choice_made="Neptune", reasoning="AWS-native",
                 decided_at="2026-04-20", decided_by="founder-1")
        d.update(kw)
        return Decision(**d)

    def test_valid(self):
        assert self._valid().status == DecisionStatus.ACTIVE.value

    def test_null_project_id_allowed(self):
        assert self._valid().project_id is None

    def test_requires_reasoning(self):
        with pytest.raises(SchemaValidationError, match="reasoning"):
            self._valid(reasoning="")

    def test_list_serializes_as_json(self):
        props = self._valid().to_neptune_props()
        assert json.loads(props["alternatives_considered"]) == ["Neo4j", "DDB"]


class TestHypothesis:
    def _valid(self, **kw):
        d = dict(BASE, object_type="Hypothesis",
                 statement="Founders pay $999/mo", why_believed="12 interviews",
                 how_will_be_tested="Beta pricing")
        d.update(kw)
        return Hypothesis(**d)

    def test_valid(self):
        assert self._valid().status == HypothesisStatus.UNVALIDATED.value

    def test_requires_statement(self):
        with pytest.raises(SchemaValidationError, match="statement"):
            self._valid(statement="")

    def test_all_statuses_accepted(self):
        for s in HypothesisStatus.values():
            assert self._valid(status=s).status == s


class TestObjectClassFor:
    def test_feature(self):
        assert object_class_for("Feature") is Feature

    def test_decision(self):
        assert object_class_for("Decision") is Decision

    def test_hypothesis(self):
        assert object_class_for("Hypothesis") is Hypothesis

    def test_unknown_raises(self):
        with pytest.raises(SchemaValidationError, match="Unknown"):
            object_class_for("NotAType")
