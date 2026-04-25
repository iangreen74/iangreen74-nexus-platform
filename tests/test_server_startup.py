"""Server-startup regression tests.

The Saturday-morning probe found Echo running toolless because nothing
called register_all_read_tools() at startup. This test catches the same
miss in the future.
"""
import os
os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

EXPECTED_READ_TOOLS = {
    "read_aws_resource",
    "read_cloudwatch_logs",
    "read_github",
    "query_pipeline_truth",
    "query_engineering_ontology",
    "read_overwatch_metrics",
}


def test_startup_registers_v2_read_tools():
    """All six V2 read tools must be registered when the FastAPI app starts."""
    from nexus.overwatch_v2.tools.registry import (
        _reset_registry_for_tests, list_tools,
    )
    _reset_registry_for_tests()

    from fastapi.testclient import TestClient
    from nexus.server import app

    with TestClient(app):
        # Inside the context manager, FastAPI startup events have fired.
        specs = list_tools(include_mutations=False)

    names = {(s.get("toolSpec") or {}).get("name") for s in specs}
    missing = EXPECTED_READ_TOOLS - names
    assert not missing, f"Expected V2 read tools missing from registry: {missing}"
    assert len(specs) >= len(EXPECTED_READ_TOOLS)


def test_startup_registration_is_idempotent():
    """Re-running TestClient(app) does not duplicate tool registrations."""
    from nexus.overwatch_v2.tools.registry import (
        _reset_registry_for_tests, list_tools,
    )
    _reset_registry_for_tests()

    from fastapi.testclient import TestClient
    from nexus.server import app

    with TestClient(app):
        first = list_tools(include_mutations=False)
    with TestClient(app):
        second = list_tools(include_mutations=False)

    names_first = sorted((s.get("toolSpec") or {}).get("name") for s in first)
    names_second = sorted((s.get("toolSpec") or {}).get("name") for s in second)
    assert names_first == names_second
    assert len(names_second) == len(set(names_second))  # no duplicates
