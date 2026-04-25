"""Echo HTTP surface — POST /api/v2/echo/chat + conversation reads.

The single curl-able interface to V2 Overwatch's reasoner. Day 6's React
frontend talks to these same endpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

log = logging.getLogger("nexus.dashboard.echo_routes")

router = APIRouter(prefix="/api/v2/echo", tags=["echo"])


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str
    operator: Optional[str] = "ian"


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    tool_calls: list[dict[str, Any]] = []
    tokens_in: int = 0
    tokens_out: int = 0
    rounds: int = 0
    error: Optional[str] = None


@router.post("/chat", response_model=ChatResponse)
async def post_chat(body: ChatRequest = Body(...)) -> ChatResponse:
    if not body.message or not body.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    from nexus.aria_v2 import reasoner
    result = reasoner.respond(
        conversation_id=body.conversation_id,
        user_message=body.message,
        operator=body.operator or "ian",
    )
    return ChatResponse(
        conversation_id=result.conversation_id,
        response=result.text,
        tool_calls=result.tool_calls_made,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        rounds=result.rounds,
        error=result.error,
    )


@router.get("/conversations")
async def list_conversations(limit: int = 50) -> dict[str, Any]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit out of range (1..200)")
    from nexus.aria_v2 import persistence
    return {"conversations": persistence.list_conversations(limit=limit)}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, limit: int = 200) -> dict[str, Any]:
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit out of range (1..1000)")
    from nexus.aria_v2 import persistence
    turns = persistence.list_turns(conversation_id, limit=limit)
    if not turns:
        raise HTTPException(status_code=404,
                            detail=f"no conversation found: {conversation_id}")
    return {"conversation_id": conversation_id, "turn_count": len(turns),
            "turns": turns}


@router.get("/health")
async def echo_health() -> dict[str, str]:
    """Lightweight echo-subsystem health (no Bedrock call)."""
    return {"status": "online", "subsystem": "echo", "version": "v2-day-5"}
