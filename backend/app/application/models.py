"""Normalised application knowledge. Anything discovery cannot find stays None / empty - never invented."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DocumentRef(BaseModel):
    path: str  # relative to the application root
    title: str | None = None
    summary: str | None = None


class ApiInfo(BaseModel):
    service: str | None  # telemetry service name, None when the API could not be tied to a service
    name: str  # e.g. "PaymentService/Charge" or "GET /products"
    kind: str  # grpc | http
    request: str | None = None
    response: str | None = None
    source: str  # file it was found in


class FailureScenario(BaseModel):
    name: str
    description: str | None = None
    service: str | None = None
    default_variant: str | None = None
    variants: list[str] = Field(default_factory=list)
    source: str
    enabled: bool | None = None  # is the scenario switched on in the configuration (None = not determinable)
    active_variant: str | None = None
    documented_in: list[str] = Field(default_factory=list)  # Markdown files that mention it by name


class ServiceInfo(BaseModel):
    name: str  # the name used in telemetry (service.name)
    kind: str = "application"  # application | infrastructure
    purpose: str | None = None
    criticality: str | None = None  # CRITICAL | HIGH | MEDIUM | LOW, only when the application declares it
    criticality_source: str | None = None
    dependencies: list[str] = Field(default_factory=list)  # services this one calls / needs
    dependents: list[str] = Field(default_factory=list)  # services that call / need this one
    apis: list[str] = Field(default_factory=list)
    documentation_references: list[str] = Field(default_factory=list)
    telemetry_information: dict = Field(default_factory=dict)
    failure_scenarios: list[str] = Field(default_factory=list)


class ApplicationKnowledge(BaseModel):
    application_name: str
    application_path: str
    summary: str | None = None
    services: list[ServiceInfo] = Field(default_factory=list)
    dependencies: list[dict] = Field(default_factory=list)  # {"from": a, "to": b, "source": file}
    apis: list[ApiInfo] = Field(default_factory=list)
    documentation: list[DocumentRef] = Field(default_factory=list)
    configuration: dict = Field(default_factory=dict)
    criticality: dict[str, str | None] = Field(default_factory=dict)  # service -> criticality
    telemetry: dict = Field(default_factory=dict)
    failure_scenarios: list[FailureScenario] = Field(default_factory=list)
    files_scanned: int = 0
    scan_warnings: list[str] = Field(default_factory=list)
    last_scanned_at: datetime

    def service(self, name: str | None) -> ServiceInfo | None:
        if not name:
            return None
        return next((s for s in self.services if s.name == name), None)


class ScanRequest(BaseModel):
    name: str
    path: str
