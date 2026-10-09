from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

INSUFFICIENT_EVIDENCE = "Insufficient evidence"


class EvidenceItem(BaseModel):
    statement: str = Field(description="One specific observation or conclusion.")
    kind: Literal["FACT", "AI_INFERENCE"] = Field(
        description="FACT: stated directly by a tool result. AI_INFERENCE: your interpretation of the facts."
    )
    source: str = Field(default="", description="Tool name or log_id that supports it (required for FACT).")


class InvestigationOutput(BaseModel):
    """What the Investigation Agent must return (its ADK output_schema)."""

    root_cause: str = Field(
        description=f'Most likely root cause in one or two sentences, or exactly "{INSUFFICIENT_EVIDENCE}".'
    )
    confidence: float = Field(description="0.0 to 1.0, calibrated to how directly the evidence supports the root cause.")
    evidence: list[EvidenceItem]
    analysis: str = Field(description="Short reasoning connecting the evidence to the root cause.")
    unknowns: list[str] = Field(description="What cannot be determined from the available data.")
    insufficient_evidence: bool
    affected_services: list[str] = Field(default_factory=list, description="Services the evidence shows are affected.")
    dependencies_involved: list[str] = Field(default_factory=list, description="Dependencies that caused or contributed, only if evidenced.")
    alternative_causes: list[str] = Field(default_factory=list, description="Other plausible causes the evidence does not rule out.")


class InvestigationRecord(InvestigationOutput):
    incident_id: str
    model: str
    provider: str = "vertex"
    tools_called: list[str] = Field(default_factory=list)
    based_on_occurrences: int
    duration_ms: int
    created_at: datetime
