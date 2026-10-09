from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ExecutionMode = Literal["AUTO_EXECUTE", "HUMAN_APPROVAL", "HUMAN_TAKEOVER"]
Level = Literal["LOW", "MEDIUM", "HIGH"]
ResolutionState = Literal["proposed", "executed", "resolved", "human_takeover"]


class ResolutionOption(BaseModel):
    option_id: str
    action: str  # a key of the guarded action registry (config/resolution_rules.ACTIONS)
    target_service: str
    title: str = ""
    description: str
    prerequisites: list[str] = Field(default_factory=list)
    expected_outcome: str = ""
    risk: Level
    impact: str
    blast_radius: str
    blast_radius_level: Level
    reversibility: Literal["REVERSIBLE", "PARTIAL", "IRREVERSIBLE"]
    confidence: float
    execution_mode: ExecutionMode
    rationale: str


class RemediationAttempt(BaseModel):
    attempt_id: str
    option_id: str
    action: str
    target_service: str
    mode: str  # simulated | executed
    status: Literal["executed", "rejected"]
    approved_by: str | None = None
    detail: str
    executed_at: datetime


class VerificationResult(BaseModel):
    attempt_id: str
    status: Literal["SUCCESS", "FAILED", "UNKNOWN"]
    reason: str
    before: dict = Field(default_factory=dict)
    after: dict = Field(default_factory=dict)
    checked_at: datetime


class ResolutionRecord(BaseModel):
    incident_id: str
    options: list[ResolutionOption]
    recommended_option_id: str | None
    decision: ExecutionMode
    decision_reason: str
    state: ResolutionState = "proposed"
    next_step: str
    based_on_investigation: bool
    attempts: list[RemediationAttempt] = Field(default_factory=list)
    verifications: list[VerificationResult] = Field(default_factory=list)
    failed_options: list[str] = Field(default_factory=list)  # option ids verified as not working
    reasoned_by: Literal["policy", "ai+policy"] = "policy"  # "ai+policy": the model chose among policy-approved options
    ai_reason: str | None = None
    validation_notes: list[str] = Field(default_factory=list)  # what the policy layer corrected or rejected
    created_at: datetime
    updated_at: datetime


class ApproveRequest(BaseModel):
    option_id: str
    approved_by: str = "operator"
