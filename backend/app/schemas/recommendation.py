from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Recommendation(BaseModel):
    title: str = Field(description="Short action title. For runbook actions, the runbook step title verbatim.")
    description: str = Field(description="What an engineer would do.")
    reason: str = Field(description="Why, tied to specific investigation evidence.")
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    source: Literal["runbook", "AI-generated"]


class RecommendationOutput(BaseModel):
    """What the Recommendation Agent must return (its ADK output_schema)."""

    recommendations: list[Recommendation]


class RecommendationRecord(RecommendationOutput):
    incident_id: str
    model: str
    runbook_id: str | None = None
    tools_called: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list, description="Deterministic corrections applied")
    duration_ms: int
    created_at: datetime
