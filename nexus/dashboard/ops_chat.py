"""
Overwatch Ops Chat — Bedrock-powered platform mechanic.

A conversational interface for platform operations. The chat has:
1. Full read access to platform state (Neptune, sensors, heal chains)
2. Execution access to all Overwatch capabilities via ACTION: directives
3. Bedrock Sonnet reasoning for diagnosis and recommendations

The operator types natural language. The chat gathers context, calls
Bedrock, and can execute capabilities when the model outputs ACTION: lines.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import boto3

from nexus.config import AWS_REGION, MODE, OPS_CHAT_MAX_TOKENS, OPS_CHAT_MODEL_ID

logger = logging.getLogger("nexus.dashboard.ops_chat")


def build_system_prompt(context: dict[str, Any]) -> str:
    """Build the system prompt with current platform state."""
    return (
        "You are the Overwatch Ops Assistant — the platform mechanic for Forgewing.\n"
        "You have FULL access to the platform state. Here is the current snapshot:\n\n"
        "PLATFORM STATUS:\n"
        f"{json.dumps(context.get('status', {}), indent=2, default=str)[:3000]}\n\n"
        "TENANT DETAILS:\n"
        f"{json.dumps(context.get('tenants', []), indent=2, default=str)[:3000]}\n\n"
        "ACTIVE HEAL CHAINS:\n"
        f"{json.dumps(context.get('heal_chains', {}), indent=2, default=str)[:1000]}\n\n"
        "TRIAGE DECISIONS (this cycle):\n"
        f"{json.dumps(context.get('executions', []), indent=2, default=str)[:1000]}\n\n"
        "FAILURE PATTERNS:\n"
        f"{json.dumps(context.get('patterns', []), indent=2, default=str)[:500]}\n\n"
        "ENGINEERING INSIGHTS (cross-tenant, anonymous):\n"
        f"{json.dumps(context.get('engineering_insights', []), indent=2, default=str)[:500]}\n\n"
        "AVAILABLE ACTIONS (you can execute these):\n"
        f"{json.dumps(context.get('capabilities', []), indent=2, default=str)[:1500]}\n\n"
        "RULES:\n"
        "- Answer with specific data from the snapshot above — never guess\n"
        "- When the operator asks you to do something, output ACTION:capability_name:param=value\n"
        "- Be direct and concise — this is an ops console, not a conversation\n"
        "- If you don't have enough data, say what data you need\n"
        "- Reference specific tenant IDs, timestamps, and counts\n"
        "- Look for concerning trends and warn proactively\n"
        "- When asked 'what should we improve?', reference engineering insights\n"
    )


def execute_action(action_str: str) -> dict[str, Any]:
    """Execute a capability referenced by the chat via ACTION: directive."""
    try:
        parts = action_str.split(":")
        capability_name = parts[0].strip()
        kwargs: dict[str, str] = {}
        for part in parts[1:]:
            if "=" in part:
                key, val = part.split("=", 1)
                kwargs[key.strip()] = val.strip()

        from nexus.capabilities.registry import registry

        cap = registry.get(capability_name)
        result = cap.function(**kwargs)

        try:
            from nexus import overwatch_graph

            overwatch_graph.record_healing_action(
                action_type=capability_name,
                target=kwargs.get("tenant_id", "ops_chat"),
                blast_radius=cap.blast_radius,
                trigger="ops_chat",
                outcome="success" if not (isinstance(result, dict) and result.get("error")) else "failed",
            )
        except Exception:
            pass

        return {"executed": capability_name, "params": kwargs, "result": result}
    except KeyError:
        return {"error": f"Unknown capability: {action_str.split(':')[0]}"}
    except Exception as exc:
        return {"error": f"Execution failed: {exc}"}


def chat(message: str, context: dict[str, Any], history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Process a chat message with full platform context."""
    if MODE != "production":
        return {
            "response": f"[Local mode] You asked: {message}\n\nIn production this queries Bedrock with full platform context.",
            "actions": [],
        }

    system_prompt = build_system_prompt(context)

    messages = []
    for h in (history or [])[-10:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    try:
        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        resp = client.invoke_model(
            modelId=OPS_CHAT_MODEL_ID,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": OPS_CHAT_MAX_TOKENS,
                "system": system_prompt,
                "messages": messages,
            }),
        )
        body = json.loads(resp["body"].read())
        assistant_text = ""
        for block in body.get("content", []):
            if block.get("type") == "text":
                assistant_text = block.get("text", "")
                break
    except Exception as exc:
        logger.exception("Bedrock call failed")
        return {"response": f"Bedrock error: {exc}", "actions": []}

    # Execute any ACTION: directives in the response
    actions_taken: list[dict[str, Any]] = []
    for match in re.findall(r"ACTION:([^\n]+)", assistant_text):
        result = execute_action(match.strip())
        actions_taken.append(result)

    clean = re.sub(r"ACTION:[^\n]+\n?", "", assistant_text).strip()

    if actions_taken:
        clean += "\n\n**Actions executed:**"
        for a in actions_taken:
            if a.get("error"):
                clean += f"\n  {a['error']}"
            else:
                r = a.get("result", {})
                summary = r.get("status") or r.get("diagnosis") or "OK" if isinstance(r, dict) else str(r)[:60]
                clean += f"\n  {a['executed']}({a.get('params', {})}) -> {summary}"

    return {"response": clean, "actions": actions_taken, "model": OPS_CHAT_MODEL_ID}
