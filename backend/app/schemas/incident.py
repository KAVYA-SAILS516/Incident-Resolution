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
    detected_at: datetime | None = None  # when this platform first detected it (kept across re-ingests)
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

    # Application context (set when an application has been scanned and the service is known).
    application: str | None = None
    service_criticality: str | None = None  # the application's own criticality for this service
    affected_services: list[str] = Field(default_factory=list)  # services seen failing in the same traces
    trace_ids: list[str] = Field(default_factory=list)
    correlated_incidents: list[dict] = Field(default_factory=list)  # {incident_id, service, shared_traces}

    priority: str = "P4"
    priority_score: float = 0.0
    priority_reasons: list[str] = Field(default_factory=list)
    priority_factors: list[PriorityFactor] = Field(default_factory=list)
    priority_reason: str = ""

    log_ids: list[str] = Field(default_factory=list, description="Source log lines grouped into this incident")


class IncidentList(BaseModel):
    total: int
    incidents: list[Incident]
