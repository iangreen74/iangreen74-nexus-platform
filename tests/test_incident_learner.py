"""Tests for incident learning loop (Class 4)."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities.incident_learner import (  # noqa: E402
    IncidentSignature,
    all_signatures,
    bootstrap_signatures,
    format_for_report,
    learn_from_incident,
    scan_all_signatures,
)


def _reset_graph():
    for v in overwatch_graph._local_store.values():
        v.clear()


def test_learn_creates_new_signature():
    _reset_graph()
    sig = learn_from_incident(
        "test_sig",
        {"event_type": "foo", "contains": ["bar"]},
        {"capability": "noop", "kwargs": {}, "description": "ok"},
    )
    assert isinstance(sig, IncidentSignature)
    assert sig.signature_id.startswith("sig_")
    assert sig.confidence == 0.5
    assert sig.match_count == 0


def test_learn_merges_duplicates():
    """Same name + same detection_key → merged, confidence increases."""
    _reset_graph()
    sig1 = learn_from_incident(
        "merge_me",
        {"event_type": "foo", "contains": ["bar"]},
        {"capability": "noop", "kwargs": {}, "description": ""},
    )
    sig2 = learn_from_incident(
        "merge_me",
        {"event_type": "foo", "contains": ["bar"]},
        {"capability": "noop", "kwargs": {}, "description": ""},
    )
    assert sig1.signature_id == sig2.signature_id
    assert sig2.confidence > sig1.confidence


def test_confidence_capped_at_max():
    _reset_graph()
    for _ in range(20):
        sig = learn_from_incident(
            "capped",
            {"event_type": "foo"},
            {"capability": "noop"},
        )
    assert sig.confidence <= 0.95


def test_scan_detects_match():
    _reset_graph()
    learn_from_incident(
        "bedrock_test",
        {"event_type": "bedrock_error", "contains": ["AccessDenied"]},
        {"capability": "fix_it"},
    )
    matches = scan_all_signatures({
        "event_type": "bedrock_error",
        "message": "AccessDeniedException for model X",
    })
    assert len(matches) == 1
    assert matches[0]["name"] == "bedrock_test"


def test_scan_no_false_positives():
    _reset_graph()
    learn_from_incident(
        "specific",
        {"event_type": "foo", "contains": ["needle"]},
        {"capability": "noop"},
    )
    # Missing needle → no match
    assert scan_all_signatures({"event_type": "foo", "message": "hay"}) == []
    # Different event_type → no match
    assert scan_all_signatures({"event_type": "bar", "message": "needle"}) == []


def test_scan_field_equals():
    _reset_graph()
    learn_from_incident(
        "field_test",
        {"event_type": "tenant_health",
         "field_equals": {"deploy_stage": "not_started"}},
        {"capability": "noop"},
    )
    assert scan_all_signatures({
        "event_type": "tenant_health", "deploy_stage": "not_started",
    })
    assert not scan_all_signatures({
        "event_type": "tenant_health", "deploy_stage": "deploying",
    })


def test_scan_empty_incident():
    _reset_graph()
    assert scan_all_signatures({}) == []
    assert scan_all_signatures(None) == []


def test_bad_query_handled_gracefully():
    """Malformed signature detection_key doesn't crash scan."""
    _reset_graph()
    # Store a signature with malformed detection_key (impossible via API but
    # covers defensive reload path)
    overwatch_graph.record_event(
        event_type="incident_signature",
        service="sig_bad",
        severity="info",
        details={"signature_id": "sig_bad", "name": "bad",
                 "detection_key": "not-json-object",
                 "fix_template": "bad",
                 "confidence": 0.5, "match_count": 0},
    )
    # Should not raise
    result = scan_all_signatures({"event_type": "foo"})
    assert isinstance(result, list)


def test_bootstrap_creates_signatures():
    _reset_graph()
    sigs = bootstrap_signatures()
    assert len(sigs) == 5
    names = {s.name for s in sigs}
    assert "bedrock_model_access_denied" in names
    assert "forgewing_401_missing_api_key" in names
    assert "deploy_stuck_not_started" in names


def test_bootstrap_idempotent():
    """Running bootstrap twice doesn't duplicate signatures."""
    _reset_graph()
    bootstrap_signatures()
    bootstrap_signatures()
    sigs = all_signatures()
    # 5 unique signatures even after two runs
    assert len(sigs) == 5


def test_all_signatures_returns_unique():
    _reset_graph()
    learn_from_incident("a", {"event_type": "x"}, {"capability": "noop"})
    learn_from_incident("a", {"event_type": "x"}, {"capability": "noop"})  # merge
    learn_from_incident("b", {"event_type": "y"}, {"capability": "noop"})
    sigs = all_signatures()
    assert len(sigs) == 2


def test_scan_increments_match_count():
    _reset_graph()
    learn_from_incident("m", {"event_type": "x"}, {"capability": "noop"})
    scan_all_signatures({"event_type": "x"})
    scan_all_signatures({"event_type": "x"})
    sigs = all_signatures()
    match = next(s for s in sigs if s.name == "m")
    assert match.match_count >= 2


def test_format_empty():
    _reset_graph()
    assert "none" in format_for_report()


def test_format_with_signatures():
    _reset_graph()
    bootstrap_signatures()
    text = format_for_report()
    assert "INCIDENT SIGNATURES" in text
    assert "bedrock_model_access_denied" in text or "forgewing" in text
