from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.agents.auto_analysis import start_reanalysis
from app.api.incidents import get_incident_or_404
from app.schemas.resolution import ApproveRequest, ResolutionRecord, VerificationResult
from app.services import remediation, resolution_service, verification
from app.services.case_agent import build_case
from app.services.communication_agent import build_message
from app.state.store import get_store

router = APIRouter(prefix="/api/incidents", tags=["resolution"])


@router.get("/{incident_id}/resolution", response_model=ResolutionRecord)
def get_resolution(incident_id: str) -> ResolutionRecord:
    store = get_store()
    get_incident_or_404(store, incident_id)
    return store.resolutions.get(incident_id) or resolution_service.refresh(store, incident_id)


@router.post("/{incident_id}/resolution/execute", response_model=ResolutionRecord)
def execute_option(incident_id: str, request: ApproveRequest) -> ResolutionRecord:
    """Human approval (or policy auto-execution) of one proposed option. Guarded and simulated."""
    store = get_store()
    get_resolution(incident_id)
    try:
        remediation.execute(store, incident_id, request.option_id, request.approved_by)
    except remediation.RemediationRejected as exc:
        raise HTTPException(409, str(exc)) from exc
    return store.resolutions[incident_id]


@router.post("/{incident_id}/resolution/takeover", response_model=ResolutionRecord)
def take_over(incident_id: str) -> ResolutionRecord:
    store = get_store()
    record = get_resolution(incident_id)
    record.state = "human_takeover"
    record.decision = "HUMAN_TAKEOVER"
    record.next_step = "A person owns this incident; the system will not act on it."
    store.save_resolution(record)
    return record


@router.post("/{incident_id}/resolution/verify", response_model=VerificationResult)
async def verify_recovery(incident_id: str) -> VerificationResult:
    store = get_store()
    get_incident_or_404(store, incident_id)
    try:
        result = await run_in_threadpool(verification.verify, store, incident_id)
    except verification.VerificationNotReady as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.status == "FAILED":
        start_reanalysis(store, incident_id)  # Investigation -> Resolution Decision again, without the failed option
    return result


@router.get("/{incident_id}/case")
def get_case(incident_id: str) -> dict:
    """Case Agent: the incident case (timeline, evidence summary, approvals, verification, final status)."""
    store = get_store()
    return build_case(store, get_incident_or_404(store, incident_id))


@router.get("/{incident_id}/communication")
def get_communication(incident_id: str) -> dict:
    """Communication Agent: a status message that follows the case and never claims more than it records."""
    store = get_store()
    return build_message(store, get_incident_or_404(store, incident_id))
