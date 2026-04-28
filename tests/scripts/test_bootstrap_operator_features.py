"""Tests for scripts/bootstrap_operator_features.py.

Validates discovery and dry-run/full-run dispatch. The actual
write_operator_feature call is mocked — Neptune-side persistence is
covered by tests/operator_features/test_persistence.py.

Loaded via importlib.util.spec_from_file_location because scripts/ isn't
on the package path; tests run from the repo root via pytest.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "bootstrap_operator_features.py"


@pytest.fixture
def boot():
    """Load the bootstrap script as a fresh module per test."""
    spec = importlib.util.spec_from_file_location(
        "bootstrap_operator_features", str(_SCRIPT_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discover_features_returns_ontology_capture_loop(boot):
    """Discovery surfaces every OperatorFeature module-level FEATURE."""
    features = boot.discover_features()
    feature_ids = [f.feature_id for f in features]
    assert "ontology_capture_loop" in feature_ids, (
        f"expected ontology_capture_loop in discovered features, "
        f"got {feature_ids}"
    )


def test_dry_run_does_not_call_write(boot, monkeypatch):
    """--dry-run must not invoke write_operator_feature."""
    write_mock = MagicMock()
    monkeypatch.setattr(boot, "write_operator_feature", write_mock)
    rc = boot.main(["--dry-run"])
    assert rc == 0
    write_mock.assert_not_called()


def test_full_run_calls_write_for_each_discovered(boot, monkeypatch):
    """Without --dry-run, write_operator_feature is called once per feature."""
    write_mock = MagicMock(return_value="node-id-stub")
    monkeypatch.setattr(boot, "write_operator_feature", write_mock)
    features = boot.discover_features()
    rc = boot.main([])
    assert rc == 0
    assert write_mock.call_count == len(features)
    # First positional arg of each call is the OperatorFeature instance.
    written_ids = [c.args[0].feature_id for c in write_mock.call_args_list]
    assert "ontology_capture_loop" in written_ids


def test_module_without_feature_constant_is_skipped_branch_present(boot):
    """The discovery path tolerates missing FEATURE attributes."""
    import inspect
    src = inspect.getsource(boot.discover_features)
    assert "has no FEATURE constant" in src, (
        "discover_features should warn-and-skip on modules missing FEATURE"
    )
    assert 'startswith("_")' in src, (
        "discover_features should skip private/underscore-prefixed modules"
    )
