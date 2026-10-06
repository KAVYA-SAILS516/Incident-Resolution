from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agents.auto_analysis import lock_for
from app.agents.investigation_agent import investigate
from app.agents.runner import AgentFailed, AgentUnavailable
from app.api.incidents import get_incident_or_404
from app.schemas.investigation import InvestigationRecord
from app.state.store import get_store

router = APIRouter(prefix="/api/incidents", tags=["investigation"])


@router.post("/{incident_id}/investigate", response_model=InvestigationRecord)
async def investigate_incident(incident_id: str) -> InvestigationRecord:
    """Explicit, human-triggered re-investigation (e.g. the "Investigate more" action). The background
    workflow (app/agents/auto_analysis.py) already runs this automatically for every incident; this
    endpoint is for re-running it on demand, and shares its lock so the two never race."""
    store = get_store()
    incident = get_incident_or_404(store, incident_id)
    async with lock_for(incident_id):
        try:
            record = await investigate(incident, store)
        except AgentUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except AgentFailed as exc:
            raise HTTPException(502, str(exc)) from exc
        store.save_investigation(record)
    return record
