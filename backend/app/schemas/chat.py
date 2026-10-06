from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatOutput(BaseModel):
    """What the Chat Agent must return (its ADK output_schema)."""

    reply: str = Field(description="A concise, plain-text answer. No markdown, no code fences.")
    referenced_incidents: list[str] = Field(
        default_factory=list, description="Incident ids (e.g. INC-AC837A74) the reply is actually about."
    )


class ChatReply(ChatOutput):
    model: str
    tools_called: list[str] = Field(default_factory=list)
    duration_ms: int
