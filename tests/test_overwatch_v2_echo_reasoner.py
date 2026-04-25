"""Tests for V2 Echo reasoner — persona, prompt assembly, Bedrock loop, endpoint.

NEXUS_MODE=local. Real Bedrock is mocked; Track E/F lazy imports are
mocked via patch.dict(sys.modules, ...).
"""
import os
os.environ.setdefault("NEXUS_MODE", "local")

import sys  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from nexus.aria_v2 import persistence, prompt_assembly, reasoner  # noqa: E402


def _reset():
    persistence.reset_local()


# === Persona loading =======================================================

class TestPersona:
    def test_persona_file_exists(self):
        assert prompt_assembly.PERSONA_PATH.exists()

    def test_persona_loads_nonempty(self):
        text = prompt_assembly._load_persona()
        assert len(text) > 1000

    def test_persona_contains_signature_phrase(self):
        text = prompt_assembly._load_persona()
        assert "You are Echo" in text

    def test_persona_ascii_only(self):
        b = prompt_assembly.PERSONA_PATH.read_bytes()
        assert all(c < 128 for c in b)

    def test_persona_starts_after_draft_marker(self):
        # Should not include the metadata header section
        text = prompt_assembly._load_persona()
        assert "## The draft prompt" in text or "You are Echo" in text


# === Six-source assembly ===================================================

class TestPromptAssembly:
    def test_assembly_never_raises_with_no_dependencies(self):
        prompt = prompt_assembly.assemble_echo_prompt(None)
        assert isinstance(prompt, str) and prompt

    def test_assembly_includes_persona(self):
        p = prompt_assembly.assemble_echo_prompt(None)
        assert "You are Echo" in p

    def test_assembly_includes_operator_section(self):
        p = prompt_assembly.assemble_echo_prompt(None)
        assert "Ian" in p

    def test_assembly_includes_sprint_section(self):
        p = prompt_assembly.assemble_echo_prompt(None)
        assert "Sprint 14" in p

    def test_ontology_section_empty_when_track_e_missing(self):
        # Force ontology import to fail
        with patch.dict(sys.modules, {"nexus.overwatch_v2.ontology": None}):
            section = prompt_assembly._ontology_section()
        assert section[1] == ""

    def test_tools_section_empty_when_registry_missing(self):
        with patch.dict(sys.modules, {"nexus.overwatch_v2.tools.registry": None}):
            section = prompt_assembly._tools_section()
        assert section[1] == ""

    def test_tools_section_lists_registered_tools(self):
        fake = MagicMock()
        fake.list_tools.return_value = [
            {"toolSpec": {"name": "read_aws_resource",
                          "description": "Read an AWS resource."}},
            {"toolSpec": {"name": "read_cloudwatch_logs",
                          "description": "Bounded log reads."}},
        ]
        with patch.dict(sys.modules, {"nexus.overwatch_v2.tools.registry": fake}):
            name, text, _ = prompt_assembly._tools_section()
        assert name == "tools"
        assert "read_aws_resource" in text
        assert "read_cloudwatch_logs" in text

    def test_history_section_uses_recent_turns(self):
        _reset()
        cid = persistence.ensure_conversation(None)
        persistence.append_turn(cid, "user", {"text": "first message"})
        persistence.append_turn(cid, "assistant", {"text": "first reply"})
        p = prompt_assembly.assemble_echo_prompt(cid)
        assert "first message" in p
        assert "first reply" in p

    def test_history_section_empty_for_new_conversation(self):
        section = prompt_assembly._history_section([])
        assert section[1] == ""


# === Token-budget trimming =================================================

class TestBudget:
    def test_under_budget_no_trim(self):
        sections = [
            ("persona", "P" * 100, 0),
            ("history", "H" * 100, 100),
        ]
        out = prompt_assembly._compose_with_budget(sections)
        assert "P" * 100 in out and "H" * 100 in out

    def test_over_budget_trims_high_priority_first(self):
        big_history = "H" * (prompt_assembly.MAX_TOTAL_TOKENS *
                             prompt_assembly.CHARS_PER_TOKEN)
        sections = [
            ("persona", "P" * 100, 0),
            ("history", big_history, 100),
        ]
        out = prompt_assembly._compose_with_budget(sections)
        assert "P" * 100 in out
        assert len(out) < len(big_history) + 200

    def test_persona_priority_zero_never_trimmed(self):
        # Persona has priority 0 so the trim loop must stop before touching it.
        big_persona = "P" * (prompt_assembly.MAX_TOTAL_TOKENS *
                             prompt_assembly.CHARS_PER_TOKEN * 2)
        sections = [("persona", big_persona, 0)]
        out = prompt_assembly._compose_with_budget(sections)
        assert out == big_persona


# === Reasoner local-mode (stub) ============================================

class TestReasonerLocal:
    def test_local_returns_stub_text(self):
        _reset()
        r = reasoner.respond(None, "diagnose the deploy")
        assert r.text == "[stub] diagnose the deploy"
        assert r.conversation_id

    def test_local_persists_user_and_assistant_turns(self):
        _reset()
        r = reasoner.respond(None, "hello")
        turns = persistence.list_turns(r.conversation_id)
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"

    def test_local_continues_existing_conversation(self):
        _reset()
        r1 = reasoner.respond(None, "first")
        r2 = reasoner.respond(r1.conversation_id, "second")
        assert r1.conversation_id == r2.conversation_id
        turns = persistence.list_turns(r1.conversation_id)
        assert len(turns) == 4

    def test_local_no_tool_calls_made(self):
        _reset()
        r = reasoner.respond(None, "hello")
        assert r.tool_calls_made == []
        assert r.rounds == 0


# === Reasoner production-mode loop (mocked Bedrock) ========================

def _bedrock_text_response(text: str, in_tokens=10, out_tokens=20) -> dict:
    return {
        "output": {"message": {"role": "assistant",
                                "content": [{"text": text}]}},
        "usage": {"inputTokens": in_tokens, "outputTokens": out_tokens},
        "stopReason": "end_turn",
    }


def _bedrock_tool_use(name: str, tool_use_id: str, input_: dict) -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": [
            {"text": ""},
            {"toolUse": {"toolUseId": tool_use_id, "name": name, "input": input_}},
        ]}},
        "usage": {"inputTokens": 5, "outputTokens": 5},
        "stopReason": "tool_use",
    }


@pytest.fixture
def prod_mode():
    with patch.object(reasoner, "_is_production", return_value=True):
        yield


class TestReasonerProductionLoop:
    def test_text_only_response(self, prod_mode):
        _reset()
        client = MagicMock()
        client.converse.return_value = _bedrock_text_response("Hello, Ian.")
        with patch.object(reasoner, "_bedrock_client", return_value=client), \
             patch.object(reasoner, "_get_tools_for_bedrock", return_value=[]):
            r = reasoner.respond(None, "ping")
        assert r.text == "Hello, Ian."
        assert r.rounds == 0
        assert r.tokens_in == 10 and r.tokens_out == 20

    def test_single_tool_call_round(self, prod_mode):
        _reset()
        client = MagicMock()
        client.converse.side_effect = [
            _bedrock_tool_use("read_aws_resource", "tu-1",
                              {"resource_type": "cfn_stack", "identifier": "x"}),
            _bedrock_text_response("Stack is healthy."),
        ]
        fake_dispatch = MagicMock(return_value=MagicMock(
            ok=True, value={"status": "CREATE_COMPLETE"}, error=None))
        fake_registry = MagicMock(dispatch=fake_dispatch,
                                  list_tools=MagicMock(return_value=[]))
        with patch.object(reasoner, "_bedrock_client", return_value=client), \
             patch.object(reasoner, "_get_tools_for_bedrock", return_value=[]), \
             patch.dict(sys.modules,
                        {"nexus.overwatch_v2.tools.registry": fake_registry}):
            r = reasoner.respond(None, "what's the stack state?")
        assert r.text == "Stack is healthy."
        assert r.rounds == 1
        assert len(r.tool_calls_made) == 1
        assert r.tool_calls_made[0]["tool_name"] == "read_aws_resource"
        assert fake_dispatch.called

    def test_tool_call_cap_at_max_rounds(self, prod_mode):
        _reset()
        # Bedrock keeps returning tool_use forever
        client = MagicMock()
        client.converse.return_value = _bedrock_tool_use(
            "read_aws_resource", "tu-loop", {"resource_type": "cfn_stack",
                                             "identifier": "x"})
        fake_dispatch = MagicMock(return_value=MagicMock(
            ok=True, value={}, error=None))
        fake_registry = MagicMock(dispatch=fake_dispatch,
                                  list_tools=MagicMock(return_value=[]))
        with patch.object(reasoner, "_bedrock_client", return_value=client), \
             patch.object(reasoner, "_get_tools_for_bedrock", return_value=[]), \
             patch.dict(sys.modules,
                        {"nexus.overwatch_v2.tools.registry": fake_registry}):
            r = reasoner.respond(None, "loop")
        assert r.error == "tool_round_cap"
        assert r.rounds == reasoner.MAX_TOOL_ROUNDS
        assert "tool-call cap" in r.text

    def test_bedrock_failure_returns_graceful_message(self, prod_mode):
        _reset()
        client = MagicMock()
        client.converse.side_effect = RuntimeError("boom")
        with patch.object(reasoner, "_bedrock_client", return_value=client), \
             patch.object(reasoner, "_get_tools_for_bedrock", return_value=[]):
            r = reasoner.respond(None, "ping")
        assert "cannot reach Bedrock" in r.text
        assert r.error and "RuntimeError" in r.error

    def test_tool_dispatch_failure_surfaces_in_outcome(self, prod_mode):
        _reset()
        client = MagicMock()
        client.converse.side_effect = [
            _bedrock_tool_use("read_aws_resource", "tu-2",
                              {"resource_type": "cfn_stack", "identifier": "x"}),
            _bedrock_text_response("I could not read the stack."),
        ]
        fake_dispatch = MagicMock(return_value=MagicMock(
            ok=False, value=None, error="ToolForbidden: AccessDenied"))
        fake_registry = MagicMock(dispatch=fake_dispatch,
                                  list_tools=MagicMock(return_value=[]))
        with patch.object(reasoner, "_bedrock_client", return_value=client), \
             patch.object(reasoner, "_get_tools_for_bedrock", return_value=[]), \
             patch.dict(sys.modules,
                        {"nexus.overwatch_v2.tools.registry": fake_registry}):
            r = reasoner.respond(None, "diagnose")
        assert len(r.tool_calls_made) == 1
        outcome = r.tool_calls_made[0]["outcome"]
        assert outcome["ok"] is False
        assert "Forbidden" in (outcome["error"] or "")

    def test_tokens_accumulate_across_rounds(self, prod_mode):
        _reset()
        client = MagicMock()
        client.converse.side_effect = [
            _bedrock_tool_use("read_aws_resource", "t", {"resource_type": "cfn_stack",
                                                          "identifier": "x"}),
            _bedrock_text_response("done", in_tokens=15, out_tokens=25),
        ]
        fake_dispatch = MagicMock(return_value=MagicMock(ok=True, value={}, error=None))
        fake_registry = MagicMock(dispatch=fake_dispatch,
                                  list_tools=MagicMock(return_value=[]))
        with patch.object(reasoner, "_bedrock_client", return_value=client), \
             patch.object(reasoner, "_get_tools_for_bedrock", return_value=[]), \
             patch.dict(sys.modules,
                        {"nexus.overwatch_v2.tools.registry": fake_registry}):
            r = reasoner.respond(None, "x")
        # Round 1 tool_use: 5/5; Round 2 final: 15/25; totals: 20/30
        assert r.tokens_in == 20
        assert r.tokens_out == 30


# === Persistence ===========================================================

class TestPersistence:
    def test_ensure_conversation_returns_uuid(self):
        _reset()
        cid = persistence.ensure_conversation(None)
        assert cid

    def test_ensure_conversation_idempotent(self):
        _reset()
        cid = persistence.ensure_conversation("11111111-1111-1111-1111-111111111111")
        assert cid == "11111111-1111-1111-1111-111111111111"
        persistence.ensure_conversation(cid)  # second time, no error
        assert len(persistence.list_conversations()) == 1

    def test_append_turn_increments_index(self):
        _reset()
        cid = persistence.ensure_conversation(None)
        t1 = persistence.append_turn(cid, "user", {"text": "a"})
        t2 = persistence.append_turn(cid, "assistant", {"text": "b"})
        assert t1["turn_index"] == 0
        assert t2["turn_index"] == 1

    def test_list_turns_returns_persisted(self):
        _reset()
        cid = persistence.ensure_conversation(None)
        persistence.append_turn(cid, "user", {"text": "a"})
        persistence.append_turn(cid, "assistant", {"text": "b"})
        turns = persistence.list_turns(cid)
        assert [t["role"] for t in turns] == ["user", "assistant"]

    def test_reset_local_clears_state(self):
        _reset()
        cid = persistence.ensure_conversation(None)
        persistence.append_turn(cid, "user", {"text": "a"})
        persistence.reset_local()
        assert persistence.list_conversations() == []
        assert persistence.list_turns(cid) == []

    def test_list_conversations_sorted_by_active(self):
        _reset()
        a = persistence.ensure_conversation(None)
        b = persistence.ensure_conversation(None)
        persistence.append_turn(b, "user", {"text": "newer"})
        convs = persistence.list_conversations()
        assert convs[0]["conversation_id"] == b


# === HTTP endpoint =========================================================

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from nexus.dashboard.echo_routes import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestEndpoint:
    def test_chat_happy_path(self, client):
        _reset()
        r = client.post("/api/v2/echo/chat",
                        json={"conversation_id": None, "message": "hello"})
        assert r.status_code == 200
        body = r.json()
        assert body["response"].startswith("[stub]")
        assert body["conversation_id"]

    def test_chat_empty_message_400(self, client):
        r = client.post("/api/v2/echo/chat",
                        json={"conversation_id": None, "message": "  "})
        assert r.status_code == 400

    def test_chat_continues_conversation(self, client):
        _reset()
        r1 = client.post("/api/v2/echo/chat",
                         json={"conversation_id": None, "message": "first"})
        cid = r1.json()["conversation_id"]
        r2 = client.post("/api/v2/echo/chat",
                         json={"conversation_id": cid, "message": "second"})
        assert r2.status_code == 200
        assert r2.json()["conversation_id"] == cid

    def test_get_conversations_lists_recent(self, client):
        _reset()
        client.post("/api/v2/echo/chat",
                    json={"conversation_id": None, "message": "hi"})
        r = client.get("/api/v2/echo/conversations")
        assert r.status_code == 200
        assert len(r.json()["conversations"]) >= 1

    def test_get_conversation_detail(self, client):
        _reset()
        r1 = client.post("/api/v2/echo/chat",
                         json={"conversation_id": None, "message": "hi"})
        cid = r1.json()["conversation_id"]
        r2 = client.get(f"/api/v2/echo/conversations/{cid}")
        assert r2.status_code == 200
        body = r2.json()
        assert body["conversation_id"] == cid
        assert body["turn_count"] >= 2

    def test_get_unknown_conversation_404(self, client):
        _reset()
        r = client.get("/api/v2/echo/conversations/does-not-exist")
        assert r.status_code == 404

    def test_health_endpoint(self, client):
        r = client.get("/api/v2/echo/health")
        assert r.status_code == 200
        assert r.json()["subsystem"] == "echo"

    def test_chat_response_includes_token_counts(self, client):
        _reset()
        r = client.post("/api/v2/echo/chat",
                        json={"conversation_id": None, "message": "hello"})
        body = r.json()
        assert "tokens_in" in body and "tokens_out" in body
