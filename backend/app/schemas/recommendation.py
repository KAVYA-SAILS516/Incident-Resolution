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


class OptionNote(BaseModel):
    option_id: str
    note: str


class ResolutionChoice(BaseModel):
    """The model's pick among the policy-approved resolution candidates. Validated by resolution_service."""

    recommended_option_id: str | None = Field(default=None, description="An option_id copied exactly from get_resolution_candidates, or null.")
    reason: str = Field(default="", description="Why this option, tied to the investigation and the evidence.")
    suggested_execution_mode: Literal["AUTO_EXECUTE", "HUMAN_APPROVAL", "HUMAN_TAKEOVER"] | None = None
    option_notes: list[OptionNote] = Field(default_factory=list, description="Optional one-line comment per candidate option_id.")


class RecommendationOutput(BaseModel):
    """What the Resolution Decision Agent must return (its ADK output_schema)."""

    recommendations: list[Recommendation]
    resolution_choice: ResolutionChoice | None = None


class RecommendationRecord(RecommendationOutput):
    incident_id: str
    model: str
    provider: str = "vertex"
    runbook_id: str | None = None
    tools_called: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list, description="Deterministic corrections applied")
    duration_ms: int
    created_at: datetime
