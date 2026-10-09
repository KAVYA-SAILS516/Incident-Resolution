from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.agents.auto_analysis import status_for
from app.schemas.incident import Incident
from app.services.evidence_service import build_evidence
from app.services.resolution_service import refresh as refresh_resolution
from app.state.store import JsonStore, get_store

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


def incident_view(incident: Incident, store: JsonStore) -> dict:
    """The incident plus the background workflow's current, backend-persisted analysis state.

    This is read-only: it reports what the workflow has done, and never starts it.
    """
    state = status_for(store, incident.incident_id)
    return {
        **incident.model_dump(mode="json", exclude={"log_ids"}),
        "analysis": {"state": state.state, "error": state.error},
    }


def get_incident_or_404(store: JsonStore, incident_id: str) -> Incident:
    incident = store.incidents.get(incident_id)
    if incident is None:
        raise HTTPException(404, f"Incident {incident_id} not found")
    return incident


@router.get("")
def list_incidents(
    priority: str | None = None,
    workflow: str | None = None,
    service: str | None = None,
    status: str | None = None,
    q: str | None = Query(default=None, description="Text search over id, title, service, error type"),
) -> dict:
    store = get_store()
    items = sorted(store.incidents.values(), key=lambda i: (-i.priority_score, i.first_seen))
    if priority:
        items = [i for i in items if i.priority == priority.upper()]
    if workflow:
        items = [i for i in items if i.workflow == workflow]
    if service:
        items = [i for i in items if i.service == service]
    if status:
        items = [i for i in items if i.status == status]
    if q:
        needle = q.lower()
        items = [i for i in items if needle in " ".join([i.incident_id, i.title, i.service, i.error_type]).lower()]
    return {"total": len(items), "incidents": [incident_view(i, store) for i in items]}


@router.get("/{incident_id}")
def get_incident(incident_id: str) -> dict:
    store = get_store()
    incident = get_incident_or_404(store, incident_id)
    evidence = build_evidence(incident, store)
    investigation = store.investigations.get(incident_id)
    recommendations = store.recommendations.get(incident_id)
    resolution = store.resolutions.get(incident_id) or refresh_resolution(store, incident_id)
    return {
        "incident": incident_view(incident, store),
        "evidence": {k: v for k, v in evidence.items() if k != "incident"},
        "investigation": investigation.model_dump(mode="json") if investigation else None,
        "recommendations": recommendations.model_dump(mode="json") if recommendations else None,
        "resolution": resolution.model_dump(mode="json"),
    }
