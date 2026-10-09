"""AI-Native Incident Resolution POC.

Deterministic Python decides WHAT happened and HOW IMPORTANT it is (parse, detect, classify, prioritise).
Two Google ADK agents reason about WHY it happened (Investigation) and WHAT COULD BE DONE (Recommendation).
A third Chat Agent answers free-form, dashboard-wide questions about incidents already on record.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.auto_analysis import start_workflow
from app.api import activity, applications, chat, dashboard, incidents, investigation, logs, recommendations, resolution, telemetry
from app.api.applications import scan_configured_application
from app.config.settings import settings
from app.state.store import get_store

app = FastAPI(title="AI-Native Incident Resolution POC", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(logs.router)
app.include_router(incidents.router)
app.include_router(investigation.router)
app.include_router(recommendations.router)
app.include_router(dashboard.router)
app.include_router(chat.router)
app.include_router(applications.router)
app.include_router(telemetry.router)
app.include_router(resolution.router)
app.include_router(activity.router)


_poll_task: list = []


@app.on_event("startup")
async def resume_background_workflow() -> None:
    """Resume analysis for any incident left unfinished by a previous run (e.g. the process restarted
    mid-analysis). Incidents already fully analysed (persisted investigation + recommendations) are
    left alone."""
    for issue in settings.configuration_issues:
        logging.getLogger(__name__).warning("Configuration: %s", issue)
    scan_configured_application()
    start_workflow(get_store())
    if settings.otel_enabled and settings.telemetry_poll_seconds > 0:
        _poll_task.append(asyncio.get_running_loop().create_task(telemetry.poll_loop()))  # keep a reference



@app.get("/health")
def health() -> dict:
    store = get_store()
    return {
        "status": "ok",
        "llm_configured": settings.llm_configured,
        "ai_provider": settings.ai_provider,
        "configuration_issues": settings.configuration_issues,
        "model": settings.model,
        "logs_ingested": len(store.logs),
        "incidents": len(store.incidents),
    }
