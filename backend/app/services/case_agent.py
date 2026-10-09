"""Case Agent: maintains the incident case, the single record of what happened and what was done about it.

Deterministic: it assembles facts the other agents and the store already persisted (nothing is generated or
guessed), so the case can always be rebuilt and never disagrees with the underlying records.
"""

from __future__ import annotations

from app.schemas.incident import Incident
from app.state.store import JsonStore

SOURCE_LABELS = {
    "detection": "OpenTelemetry Logs",
    "investigation": "Gemini / Vertex AI",
    "resolution": "Policy + Gemini / Vertex AI",
    "remediation": "Guarded remediation (simulated)",
    "verification": "OpenTelemetry telemetry (Prometheus / logs)",
}


def final_status(store: JsonStore, incident_id: str) -> str:
    resolution = store.resolutions.get(incident_id)
    if resolution is None:
        return "OPEN"
    if resolution.state == "resolved":
        return "RESOLVED"
    if resolution.state == "human_takeover":
        return "HUMAN_TAKEOVER"
    if resolution.state == "executed":
        return "VERIFYING"
    return "AWAITING_DECISION" if resolution.decision != "AUTO_EXECUTE" else "AWAITING_EXECUTION"


def build_case(store: JsonStore, incident: Incident) -> dict:
    iid = incident.incident_id
    investigation = store.investigations.get(iid)
    resolution = store.resolutions.get(iid)
    timeline = [
        {"at": incident.first_seen, "stage": "Detection", "source": SOURCE_LABELS["detection"],
         "summary": f"{incident.occurrences} {incident.error_type} events on {incident.service} began; priority {incident.priority}."},
    ]
    if investigation:
        timeline.append({"at": investigation.created_at, "stage": "Investigation", "source": SOURCE_LABELS["investigation"],
                         "summary": f"Probable root cause: {investigation.root_cause} (confidence {investigation.confidence:.0%}, "
                                    f"{investigation.provider})."})
    if resolution:
        timeline.append({"at": resolution.created_at, "stage": "Resolution decision", "source": SOURCE_LABELS["resolution"],
                         "summary": f"{resolution.decision}: {resolution.decision_reason}"})
        timeline += [{"at": a.executed_at, "stage": "Remediation", "source": SOURCE_LABELS["remediation"],
                      "summary": f"{a.option_id} {a.status} ({a.mode}); approved by {a.approved_by or 'n/a'}."} for a in resolution.attempts]
        timeline += [{"at": v.checked_at, "stage": "Verification", "source": SOURCE_LABELS["verification"],
                      "summary": f"{v.status}: {v.reason}"} for v in resolution.verifications]
    timeline.sort(key=lambda e: e["at"])
    return {
        "incident_id": iid,
        "application": incident.application,
        "observability_source": "OpenTelemetry",
        "priority": incident.priority,
        "priority_reason": incident.priority_reason,
        "severity": incident.severity,
        "service": incident.service,
        "service_criticality": incident.service_criticality,
        "affected_services": incident.affected_services,
        "root_cause": ({"text": investigation.root_cause, "confidence": investigation.confidence,
                        "insufficient_evidence": investigation.insufficient_evidence, "provider": investigation.provider,
                        "model": investigation.model, "unknowns": investigation.unknowns} if investigation else None),
        "resolution": ({"decision": resolution.decision, "state": resolution.state,
                        "recommended_option_id": resolution.recommended_option_id, "failed_options": resolution.failed_options,
                        "reasoned_by": resolution.reasoned_by} if resolution else None),
        "remediation": [a.model_dump(mode="json") for a in resolution.attempts] if resolution else [],
        "approvals": [{"option_id": a.option_id, "approved_by": a.approved_by, "at": a.executed_at.isoformat()}
                      for a in (resolution.attempts if resolution else [])],
        "verification": [v.model_dump(mode="json") for v in resolution.verifications] if resolution else [],
        "timeline": [{**e, "at": e["at"].isoformat()} for e in timeline],
        "final_status": final_status(store, iid),
    }
