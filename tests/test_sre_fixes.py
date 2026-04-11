"""Tests for SRE metrics fixes — availability + antifragile score."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.sensors.sre_metrics import (  # noqa: E402
    compute_antifragile_score,
    compute_availability,
)


@pytest.fixture(autouse=True)
def _clean():
    overwatch_graph.reset_local_store()
    yield
    overwatch_graph.reset_local_store()


def test_availability_100_no_incidents():
    avail = compute_availability(24)
    assert avail == 100.0


def test_availability_never_negative():
    # Create many open incidents to try to push availability negative
    for i in range(10):
        overwatch_graph.open_incident(f"source-{i}", "test")
    avail = compute_availability(24)
    assert avail >= 0.0


def test_availability_capped_at_100():
    avail = compute_availability(24)
    assert avail <= 100.0


def test_availability_with_resolved_incident():
    overwatch_graph.open_incident("test", "test")
    overwatch_graph.resolve_incident("test", auto_healed=True)
    avail = compute_availability(24)
    # Should be close to 100% since the incident was very short
    assert 99.0 <= avail <= 100.0


def test_antifragile_score_range():
    score = compute_antifragile_score()
    assert 0 <= score <= 100


def test_antifragile_with_patterns():
    # Add a pattern with high occurrence count
    overwatch_graph.record_failure_pattern(
        name="test_pattern",
        signature="test",
        diagnosis="test",
        resolution="test",
        confidence=0.8,
    )
    # Record >100 matches via occurrence increment
    for _ in range(101):
        overwatch_graph.record_failure_pattern(
            name="test_pattern", signature="test",
            diagnosis="test", resolution="test",
        )
    score = compute_antifragile_score()
    # Should get points for patterns learned + volume
    assert score >= 30


def test_antifragile_score_not_zero_with_data():
    """With patterns and good availability, score should be meaningful."""
    overwatch_graph.record_failure_pattern(
        name="p1", signature="s", diagnosis="d", resolution="r", confidence=0.8)
    score = compute_antifragile_score()
    assert score > 0
