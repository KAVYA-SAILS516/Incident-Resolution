from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agents.auto_analysis import lock_for
from app.agents.recommendation_agent import recommend
from app.agents.runner import AgentFailed, AgentUnavailable
from app.api.incidents import get_incident_or_404
from app.schemas.recommendation import RecommendationRecord
from app.state.store import get_store

router = APIRouter(prefix="/api/incidents", tags=["recommendations"])


@router.post("/{incident_id}/recommendations", response_model=RecommendationRecord)
async def recommend_for_incident(incident_id: str) -> RecommendationRecord:
    """Explicit, human-triggered re-run (e.g. after "Investigate more"). See investigation.py."""
    store = get_store()
    incident = get_incident_or_404(store, incident_id)
    async with lock_for(incident_id):
        investigation = store.investigations.get(incident_id)
        if investigation is None:
            raise HTTPException(409, "Investigate the incident first: POST /api/incidents/{id}/investigate")
        try:
            record = await recommend(incident, investigation, store)
        except AgentUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except AgentFailed as exc:
            raise HTTPException(502, str(exc)) from exc
        store.save_recommendations(record)
    return record
