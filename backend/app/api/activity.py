from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app.agents import ai_audit
from app.state.store import get_store

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("")
def agent_activity() -> dict:
    """What the agents and guarded actions have done, newest first (read from persisted records)."""
    store = get_store()
    events = []
    for r in store.investigations.values():
        events.append({"at": r.created_at, "incident_id": r.incident_id, "stage": "Investigation",
                       "summary": f"Root cause: {r.root_cause} (confidence {r.confidence:.0%})", "model": r.model})
    for r in store.recommendations.values():
        events.append({"at": r.created_at, "incident_id": r.incident_id, "stage": "Resolution decision (AI)",
                       "summary": f"{len(r.recommendations)} recommendation(s)", "model": r.model})
    for r in store.resolutions.values():
        events.append({"at": r.created_at, "incident_id": r.incident_id, "stage": "Resolution decision",
                       "summary": f"{r.decision}: {r.decision_reason}", "model": None})
        for a in r.attempts:
            events.append({"at": a.executed_at, "incident_id": r.incident_id, "stage": "Remediation",
                           "summary": f"{a.status} ({a.mode}): {a.detail}", "model": None})
        for v in r.verifications:
            events.append({"at": v.checked_at, "incident_id": r.incident_id, "stage": "Verification",
                           "summary": f"{v.status}: {v.reason}", "model": None})
    for call in ai_audit.recent():
        outcome = "ok" if call["success"] else f"FAILED ({call['error']})"
        events.append({"at": datetime.fromisoformat(call["at"]), "incident_id": "", "stage": "AI call",
                       "summary": f"{call['agent']} via {call['provider']} / {call['model']}: {outcome}, "
                                  f"{call['duration_ms'] / 1000:.1f}s, output {call['validation']}, context {call['context_chars']} chars",
                       "model": call["model"]})
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"total": len(events), "events": [{**e, "at": e["at"].isoformat()} for e in events[:200]]}
