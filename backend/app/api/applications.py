from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.agents.auto_analysis import start_workflow
from app.application.discovery import discover_application
from app.application.knowledge import answer_question
from app.application.models import ApplicationKnowledge, ScanRequest
from app.application.reader import ApplicationPathError
from app.config import priority_rules
from app.config.settings import settings
from app.services.ingestion import ingest
from app.state.store import get_store
from app.telemetry import otel

router = APIRouter(prefix="/api", tags=["applications"])

# Outcome of the startup / last scan of the configured application (None = fine or never attempted).
scan_error: str | None = None


class AskRequest(BaseModel):
    question: str


def policies() -> list[dict]:
    """P1-P4 as configured in config/priority_rules.py (the single source of the thresholds)."""
    return [{"priority": p, "minimum_score": minimum} for p, minimum in priority_rules.PRIORITY_THRESHOLDS]


def application_view(knowledge: ApplicationKnowledge | None) -> dict:
    return {
        "configured_name": settings.application_name,
        "configured_path": settings.application_path,
        "scan_error": scan_error,
        "configuration_issues": settings.configuration_issues,
        "observability": "OpenTelemetry",
        "scanned": knowledge is not None,
        "knowledge": knowledge.model_dump(mode="json") if knowledge else None,
        "telemetry_runtime": otel.status(),
        "telemetry_health": otel.health() if knowledge or settings.otel_enabled else None,
        "service_health": otel.service_health() if knowledge else [],
        "policies": policies(),
        "weights": priority_rules.WEIGHTS,
        "service_criticality_weight": priority_rules.SERVICE_CRITICALITY_WEIGHT,
    }


@router.post("/applications/scan")
async def scan_application(request: ScanRequest) -> dict:
    """Read-only discovery of the application at `path` (never executes anything from it). Incidents already
    detected are re-scored with the new application context."""
    if not request.name.strip():
        raise HTTPException(400, "Application name is required")
    global scan_error
    try:
        knowledge = await run_in_threadpool(discover_application, request.name, request.path, settings.application_root)
    except ApplicationPathError as exc:
        scan_error = str(exc)
        raise HTTPException(400, str(exc)) from exc
    scan_error = None
    store = get_store()
    store.save_application(knowledge)
    if store.logs or store.summary:
        await run_in_threadpool(ingest, store)
        start_workflow(store)
    return application_view(knowledge)


@router.get("/applications")
def get_application() -> dict:
    return application_view(get_store().application)


@router.post("/knowledge/ask")
def ask_knowledge(request: AskRequest) -> dict:
    """Deterministic answers to application questions, from the scanned model only."""
    return answer_question(get_store().application, request.question)


def scan_configured_application() -> None:
    """At startup: discover the application at APPLICATION_PATH, if configured. A failure is reported on the
    Applications page; it never substitutes another application or sample data."""
    global scan_error
    if not settings.application_path:
        return
    try:
        get_store().save_application(
            discover_application(settings.application_name, settings.application_path, settings.application_root))
        scan_error = None
    except ApplicationPathError as exc:
        scan_error = f"Application unavailable: {exc}"
