from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

ABNORMAL_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL"})


class LogEntry(BaseModel):
    """One normalised log line."""

    log_id: str  # "<source file>:<line number>"
    source: str
    line_number: int
    timestamp: datetime  # UTC
    level: str
    service: str
    environment: str | None = None
    host: str | None = None
    request_id: str | None = None
    client_id: str | None = None
    method: str | None = None
    endpoint: str | None = None
    status_code: int | None = None
    response_time_ms: float | None = None
    error_type: str | None = None
    message: str | None = None
    raw_message: str  # the original line, unchanged
    # OpenTelemetry context (None for plain log files).
    application: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    attributes: dict = Field(default_factory=dict)

    @property
    def is_abnormal(self) -> bool:
        return (
            self.level in ABNORMAL_LEVELS
            or self.error_type is not None
            or (self.status_code is not None and self.status_code >= 500)
        )


class ParseFailure(BaseModel):
    source: str
    line_number: int
    reason: str
    raw_line: str


class ParseResult(BaseModel):
    source: str
    total_lines: int = 0
    entries: list[LogEntry] = Field(default_factory=list)
    failures: list[ParseFailure] = Field(default_factory=list)


class IngestSummary(BaseModel):
    sources: list[str]
    total_lines: int
    parsed_lines: int
    failed_lines: int
    error_logs: int  # ERROR + CRITICAL
    warning_logs: int
    services: list[str]
    environments: list[str]
    time_range: dict[str, datetime | None]
    incidents_detected: int
    below_threshold_groups: int  # error groups too small to open an incident
    failures_sample: list[ParseFailure] = Field(default_factory=list)
    ignored_services: list[str] = Field(default_factory=list)  # telemetry from services outside the scanned application
    ingested_at: datetime
