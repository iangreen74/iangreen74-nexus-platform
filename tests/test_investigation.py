"""Tests for the Tier 1 Investigation pipeline."""
import asyncio
import os
from unittest.mock import patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import investigation as inv  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# --- registry ----------------------------------------------------------------


def test_six_gatherers_registered():
    assert set(inv._GATHERERS.keys()) == {
        "cloudwatch", "ecs", "neptune", "github_ci", "synthetic", "platform_events"
    }


# --- _strip_fences -----------------------------------------------------------


def test_strip_fences_handles_json_block():
    assert inv._strip_fences('```json\n["a","b"]\n```') == '["a","b"]'


def test_strip_fences_handles_plain():
    assert inv._strip_fences('["a","b"]') == '["a","b"]'


def test_strip_fences_handles_empty():
    assert inv._strip_fences("") == ""


# --- classifier --------------------------------------------------------------


def test_classifier_local_mode_returns_all_sources():
    sources = _run(inv._classify("any question"))
    assert set(sources) == set(inv._GATHERERS.keys())


def test_classifier_filters_unknown_sources():
    """If Bedrock returns sources we don't have, drop them."""
    with patch.object(inv, "MODE", "production"), \
         patch.object(inv, "_invoke_bedrock", return_value='["ecs","not_a_source","neptune"]'):
        sources = _run(inv._classify("test"))
    assert sources == ["ecs", "neptune"]


def test_classifier_falls_back_on_bad_json():
    """Bedrock returning garbage falls back to all sources."""
    with patch.object(inv, "MODE", "production"), \
         patch.object(inv, "_invoke_bedrock", return_value="not json at all"):
        sources = _run(inv._classify("test"))
    assert set(sources) == set(inv._GATHERERS.keys())


def test_classifier_falls_back_on_exception():
    with patch.object(inv, "MODE", "production"), \
         patch.object(inv, "_invoke_bedrock", side_effect=RuntimeError("nope")):
        sources = _run(inv._classify("test"))
    assert set(sources) == set(inv._GATHERERS.keys())


def test_classifier_empty_array_falls_back():
    with patch.object(inv, "MODE", "production"), \
         patch.object(inv, "_invoke_bedrock", return_value="[]"):
        sources = _run(inv._classify("test"))
    assert set(sources) == set(inv._GATHERERS.keys())


# --- synthesizer -------------------------------------------------------------


def test_synthesizer_local_mode_returns_stub():
    d = _run(inv._synthesize("q", {"ecs": {}}))
    assert d["confidence"] == 0
    assert "Local mode" in d["root_cause"]


def test_synthesizer_handles_bedrock_failure():
    with patch.object(inv, "MODE", "production"), \
         patch.object(inv, "_invoke_bedrock", side_effect=RuntimeError("model down")):
        d = _run(inv._synthesize("q", {"ecs": {}}))
    assert d["confidence"] == 0
    assert "Synthesis failed" in d["root_cause"]
    assert d["recommended_actions"][0]["type"] == "investigate_further"


def test_synthesizer_strips_markdown_fences():
    payload = '```json\n{"root_cause":"r","explanation":"e","confidence":80,"severity":"high","recommended_actions":[],"evidence_used":[],"evidence_gaps":[]}\n```'
    with patch.object(inv, "MODE", "production"), \
         patch.object(inv, "_invoke_bedrock", return_value=payload):
        d = _run(inv._synthesize("q", {"ecs": {}}))
    assert d["confidence"] == 80
    assert d["severity"] == "high"


# --- orchestrator ------------------------------------------------------------


def test_investigate_requires_question():
    r = _run(inv.investigate(""))
    assert r.get("error") == "question is required"


def test_investigate_returns_full_shape():
    r = _run(inv.investigate("Why is the platform degraded?"))
    assert r["question"] == "Why is the platform degraded?"
    assert r["tier"] == 1
    assert "sources_requested" in r
    assert "sources_returned" in r
    assert "diagnosis" in r
    assert "evidence" in r
    assert "duration_seconds" in r
    assert isinstance(r["duration_seconds"], (int, float))


def test_investigate_one_failing_gatherer_does_not_kill_others():
    """A gatherer raising must not block siblings — its error becomes evidence."""
    async def boom(*_a, **_k):
        raise RuntimeError("source down")
    with patch.dict(inv._GATHERERS, {"cloudwatch": boom}, clear=False):
        r = _run(inv.investigate("test question"))
    # cloudwatch evidence should be the error, others should still be present
    assert "error" in r["evidence"].get("cloudwatch", {})
    assert "RuntimeError" in r["evidence"]["cloudwatch"]["error"]
    # at least one other gatherer ran
    other_keys = [k for k in r["evidence"] if k != "cloudwatch" and not k.startswith("_")]
    assert len(other_keys) >= 1


def test_investigate_uses_classifier_subset():
    """When classifier picks a subset, only those gatherers fire."""
    async def fake_classify(_q):
        return ["ecs", "synthetic"]
    with patch.object(inv, "_classify", fake_classify):
        r = _run(inv.investigate("just check ecs"))
    assert set(r["sources_requested"]) == {"ecs", "synthetic"}
    assert set(r["sources_returned"]).issubset({"ecs", "synthetic"})
