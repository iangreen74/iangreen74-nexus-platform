"""Echo reasoner — Bedrock Converse loop with tool-calling.

NEXUS_MODE=local short-circuits Bedrock with a deterministic stub. Real
mode invokes us.anthropic.claude-sonnet-4-6 (per nexus.config) with the
tool schemas from Track F's registry.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("nexus.aria_v2.reasoner")

MAX_TOOL_ROUNDS = 8
MODEL_ID = os.environ.get("OVERWATCH_V2_MODEL_ID") or "us.anthropic.claude-sonnet-4-6"
MAX_TOKENS = int(os.environ.get("OVERWATCH_V2_MAX_TOKENS", "4096"))


@dataclass
class EchoResponse:
    conversation_id: str
    text: str
    tool_calls_made: list[dict] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    rounds: int = 0
    error: Optional[str] = None


def _is_production() -> bool:
    return os.environ.get("NEXUS_MODE", "local").lower() == "production"


def _stub_response(user_message: str) -> tuple[str, int, int]:
    text = f"[stub] {user_message}"
    return text, len(user_message) // 4, len(text) // 4


def _bedrock_client():
    from nexus.aws_client import _client
    return _client("bedrock-runtime")


def _get_tools_for_bedrock() -> list[dict]:
    try:
        from nexus.overwatch_v2.tools.registry import list_tools
        return list_tools(include_mutations=False) or []
    except Exception:
        log.exception("tool list failed; running without tools")
        return []


def _dispatch_tool(name: str, params: dict, actor: str) -> dict:
    try:
        from nexus.overwatch_v2.tools.registry import dispatch
        result = dispatch(name, params, approval_token=None, actor=actor)
        return {"ok": result.ok, "value": result.value, "error": result.error}
    except Exception as exc:
        return {"ok": False, "value": None, "error": f"{type(exc).__name__}: {exc}"}


def _converse(system_prompt: str, messages: list[dict],
              tools: list[dict]) -> dict:
    """Single Bedrock Converse call. Caller orchestrates the tool loop."""
    body: dict[str, Any] = {
        "modelId": MODEL_ID,
        "system": [{"text": system_prompt}],
        "messages": messages,
        "inferenceConfig": {"maxTokens": MAX_TOKENS},
    }
    if tools:
        body["toolConfig"] = {"tools": tools}
    return _bedrock_client().converse(**body)


def _extract_text_and_tool_uses(content: list[dict]) -> tuple[str, list[dict]]:
    text_parts, tool_uses = [], []
    for block in content or []:
        if "text" in block:
            text_parts.append(block["text"])
        if "toolUse" in block:
            tool_uses.append(block["toolUse"])
    return ("\n".join(text_parts).strip(), tool_uses)


def respond(
    conversation_id: Optional[str],
    user_message: str,
    operator: str = "ian",
) -> EchoResponse:
    from nexus.aria_v2 import persistence, prompt_assembly

    cid = persistence.ensure_conversation(conversation_id)
    persistence.append_turn(cid, "user", {"text": user_message})

    if not _is_production():
        text, ti, to = _stub_response(user_message)
        persistence.append_turn(cid, "assistant", {"text": text},
                                tokens_in=ti, tokens_out=to)
        return EchoResponse(conversation_id=cid, text=text,
                            tokens_in=ti, tokens_out=to, rounds=0)

    system_prompt = prompt_assembly.assemble_echo_prompt(cid)
    messages = [{"role": "user", "content": [{"text": user_message}]}]
    tools = _get_tools_for_bedrock()
    tool_calls_made: list[dict] = []
    total_in = total_out = 0

    for round_idx in range(MAX_TOOL_ROUNDS + 1):
        if round_idx == MAX_TOOL_ROUNDS:
            cap_msg = ("I hit the tool-call cap for this turn "
                       f"({MAX_TOOL_ROUNDS} rounds). Asking again with a "
                       "narrower question may help.")
            persistence.append_turn(cid, "assistant", {"text": cap_msg},
                                    tool_calls=tool_calls_made,
                                    tokens_in=total_in, tokens_out=total_out)
            return EchoResponse(conversation_id=cid, text=cap_msg,
                                tool_calls_made=tool_calls_made,
                                tokens_in=total_in, tokens_out=total_out,
                                rounds=round_idx, error="tool_round_cap")
        try:
            resp = _converse(system_prompt, messages, tools)
        except Exception as exc:
            log.exception("Bedrock converse failed")
            err_text = "I cannot reach Bedrock right now. Please retry."
            persistence.append_turn(cid, "assistant", {"text": err_text},
                                    tokens_in=total_in, tokens_out=total_out)
            return EchoResponse(conversation_id=cid, text=err_text,
                                tokens_in=total_in, tokens_out=total_out,
                                rounds=round_idx,
                                error=f"{type(exc).__name__}: {exc}")
        usage = (resp.get("usage") or {})
        total_in += int(usage.get("inputTokens") or 0)
        total_out += int(usage.get("outputTokens") or 0)
        out_msg = (resp.get("output") or {}).get("message") or {}
        content = out_msg.get("content") or []
        text, tool_uses = _extract_text_and_tool_uses(content)

        if not tool_uses:
            persistence.append_turn(cid, "assistant", {"text": text},
                                    tokens_in=total_in, tokens_out=total_out)
            return EchoResponse(conversation_id=cid, text=text,
                                tool_calls_made=tool_calls_made,
                                tokens_in=total_in, tokens_out=total_out,
                                rounds=round_idx)

        messages.append({"role": "assistant", "content": content})
        tool_results_blocks = []
        for tu in tool_uses:
            tool_name = tu.get("name", "?")
            tool_input = tu.get("input", {}) or {}
            tu_id = tu.get("toolUseId") or str(uuid.uuid4())
            outcome = _dispatch_tool(tool_name, tool_input, actor=operator)
            tool_calls_made.append({
                "tool_use_id": tu_id, "tool_name": tool_name,
                "input": tool_input, "outcome": outcome,
            })
            tool_results_blocks.append({"toolResult": {
                "toolUseId": tu_id,
                "content": [{"json": outcome}],
                "status": "success" if outcome.get("ok") else "error",
            }})
        persistence.append_turn(cid, "tool", {"results": tool_results_blocks},
                                tool_calls=tool_calls_made[-len(tool_uses):])
        messages.append({"role": "user", "content": tool_results_blocks})

    # Unreachable, kept for type-checker calm.
    return EchoResponse(conversation_id=cid, text="",
                        rounds=MAX_TOOL_ROUNDS, error="unreachable")
