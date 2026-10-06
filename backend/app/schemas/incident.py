from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

IncidentStatus = Literal["open", "investigated", "recommendations_ready"]


class PriorityFactor(BaseModel):
    name: str
    value: str  # the observed input, human readable
    normalized: float  # 0..1
    weight: float
    points: float  # contribution to the 0..100 score


class Incident(BaseModel):
    incident_id: str
    status: IncidentStatus = "open"
    title: str
    service: str
    environment: str | None
    error_type: str
    status_code: int | None  # most frequent status code
    endpoint_family: str
    affected_endpoints: list[str]
    occurrences: int
    first_seen: datetime
    last_seen: datetime
    duration_seconds: int
    severity: str  # highest log level seen
    levels: dict[str, int]
    status_codes: dict[str, int]
    hosts: list[str]
    distinct_clients: int
    peak_per_minute: int

    workflow: str = "Unknown"
    workflow_criticality: str = "UNKNOWN"
    workflow_reason: str = ""

    priority: str = "P4"
    priority_score: float = 0.0
    priority_reasons: list[str] = Field(default_factory=list)
    priority_factors: list[PriorityFactor] = Field(default_factory=list)

    log_ids: list[str] = Field(default_factory=list, description="Source log lines grouped into this incident")


class IncidentList(BaseModel):
    total: int
    incidents: list[Incident]
