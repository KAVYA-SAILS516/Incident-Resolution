"""AI-Native Incident Resolution POC.

Deterministic Python decides WHAT happened and HOW IMPORTANT it is (parse, detect, classify, prioritise).
Two Google ADK agents reason about WHY it happened (Investigation) and WHAT COULD BE DONE (Recommendation).
A third Chat Agent answers free-form, dashboard-wide questions about incidents already on record.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.auto_analysis import start_workflow
from app.api import chat, dashboard, incidents, investigation, logs, recommendations
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


@app.on_event("startup")
async def resume_background_workflow() -> None:
    """Resume analysis for any incident left unfinished by a previous run (e.g. the process restarted
    mid-analysis). Incidents already fully analysed (persisted investigation + recommendations) are
    left alone."""
    start_workflow(get_store())


@app.get("/health")
def health() -> dict:
    store = get_store()
    return {
        "status": "ok",
        "llm_configured": settings.llm_configured,
        "model": settings.model,
        "logs_ingested": len(store.logs),
        "incidents": len(store.incidents),
    }
