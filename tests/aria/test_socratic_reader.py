"""Tests for nexus.aria.socratic_reader."""
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.aria.socratic_reader import (
    build_socratic_section,
    read_pending_socratic_prompts,
)


def test_build_empty_list_returns_empty():
    name, text, pri = build_socratic_section([])
    assert text == ""
    assert name == "socratic"


def test_build_single_prompt():
    prompts = [{"question": "Want to test that hypothesis?"}]
    name, text, pri = build_socratic_section(prompts)
    assert "What you might want to think about" in text
    assert "Want to test that hypothesis" in text


def test_build_multiple_prompts():
    prompts = [{"question": "Q1"}, {"question": "Q2"}]
    _, text, _ = build_socratic_section(prompts)
    assert "- Q1" in text
    assert "- Q2" in text


def test_build_skips_empty_questions():
    prompts = [{"question": "Real"}, {"question": ""}, {"question": "  "}]
    _, text, _ = build_socratic_section(prompts)
    assert text.count("- ") == 1


def test_read_pending_no_db_returns_empty():
    with patch("nexus.aria.socratic_reader._pg_connect", return_value=None):
        assert read_pending_socratic_prompts("t-1") == []


def test_read_pending_db_error_returns_empty():
    with patch("nexus.aria.socratic_reader._pg_connect",
               side_effect=RuntimeError("db down")):
        assert read_pending_socratic_prompts("t-1") == []


def test_read_pending_happy_path():
    mock_conn = MagicMock()
    expected = [{"id": 1, "question": "test?"}]
    with patch("nexus.aria.socratic_reader._pg_connect",
               return_value=mock_conn), \
         patch("nexus.mechanism3.store.read_pending_prompts",
               return_value=expected) as m:
        result = read_pending_socratic_prompts("t-1", limit=5)
        assert result == expected
        m.assert_called_once()


def test_assemble_includes_socratic_when_present(monkeypatch):
    monkeypatch.setattr(
        "nexus.aria.prompt_assembly.read_pending_socratic_prompts",
        lambda tid, limit=3: [{"question": "Have you tested the auth flow?"}],
    )
    from nexus.aria.prompt_assembly import assemble_aria_prompt
    prompt = assemble_aria_prompt("t-1", None, [], [])
    assert "Have you tested the auth flow" in prompt


def test_assemble_no_socratic_when_empty(monkeypatch):
    monkeypatch.setattr(
        "nexus.aria.prompt_assembly.read_pending_socratic_prompts",
        lambda tid, limit=3: [],
    )
    from nexus.aria.prompt_assembly import assemble_aria_prompt
    prompt = assemble_aria_prompt("t-1", None, [], [])
    assert "What you might want to think about" not in prompt


def test_socratic_priority_lower_than_history():
    _, _, pri = build_socratic_section([{"question": "test"}])
    assert pri == 60  # lower than history's 100
