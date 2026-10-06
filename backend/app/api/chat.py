from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.chat_agent import chat as run_chat
from app.agents.runner import AgentFailed, AgentUnavailable
from app.schemas.chat import ChatReply, ChatTurn
from app.state.store import get_store

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    history: list[ChatTurn] = Field(default_factory=list)


@router.post("/chat", response_model=ChatReply)
async def chat_endpoint(body: ChatRequest) -> ChatReply:
    if not body.message.strip():
        raise HTTPException(400, "message must not be empty")
    try:
        return await run_chat(body.message, body.history, get_store())
    except AgentUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except AgentFailed as exc:
        raise HTTPException(502, str(exc)) from exc
