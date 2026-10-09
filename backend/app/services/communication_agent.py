"""Communication Agent: turns the incident case into a clear status message.

Template-based and deterministic on purpose: a status update must never claim more than the case records, so
the wording follows the case (UNKNOWN stays UNKNOWN, a low-confidence cause is called "possible") and no model
is involved.
"""

from __future__ import annotations

from app.schemas.incident import Incident
from app.services.case_agent import build_case
from app.state.store import JsonStore

STATUS_TEXT = {
    "OPEN": "Under investigation.",
    "AWAITING_DECISION": "A resolution has been proposed and is waiting for a person's approval.",
    "AWAITING_EXECUTION": "A resolution is ready to run under policy.",
    "VERIFYING": "A remediation action was carried out; recovery is being verified from telemetry.",
    "RESOLVED": "Resolved: recovery was verified from telemetry.",
    "HUMAN_TAKEOVER": "A person has taken over; automated remediation has stopped.",
}


def build_message(store: JsonStore, incident: Incident) -> dict:
    case = build_case(store, incident)
    cause = case["root_cause"]
    if cause is None:
        cause_text = "The probable root cause is UNKNOWN: the investigation has not completed."
    elif cause["insufficient_evidence"] or cause["confidence"] < 0.5:
        cause_text = f"A possible cause (low confidence, {cause['confidence']:.0%}) is: {cause['text']}"
    else:
        cause_text = f"The probable root cause is: {cause['text']} (confidence {cause['confidence']:.0%})."
    verification = case["verification"][-1] if case["verification"] else None
    verification_text = (f"Latest verification: {verification['status']} - {verification['reason']}" if verification
                         else "Recovery has not been verified yet.")
    resolution = case["resolution"]
    selected = resolution["recommended_option_id"] if resolution else None
    steps = {
        "OPEN": "Wait for the investigation and resolution proposal.",
        "AWAITING_DECISION": "An approver reviews the options and approves one, or takes over.",
        "AWAITING_EXECUTION": "The action runs and recovery is verified.",
        "VERIFYING": "Verify recovery once enough new telemetry has arrived.",
        "RESOLVED": "Close the case and capture lessons learned.",
        "HUMAN_TAKEOVER": "The owner continues manually.",
    }[case["final_status"]]
    others = [s for s in case["affected_services"] if s != case["service"]]
    body = "\n".join([
        f"What happened: {incident.error_type} errors on {case['service']} ({case['application'] or 'unknown application'}), "
        f"{incident.occurrences} events since {incident.first_seen:%Y-%m-%d %H:%M} UTC.",
        f"Affected services: {case['service']}" + (f"; also failing in the same requests: {', '.join(others)}." if others else "."),
        f"Impact: priority {case['priority']} - {case['priority_reason']}" if case["priority_reason"] else f"Impact: priority {case['priority']}.",
        cause_text,
        f"Status: {STATUS_TEXT[case['final_status']]}",
        f"Selected resolution: {selected or 'none yet'}.",
        verification_text,
        f"Next steps: {steps}",
    ])
    return {"incident_id": case["incident_id"], "subject": f"[{case['priority']}] {incident.title}",
            "status": case["final_status"], "body": body,
            "generated_by": "template from the incident case (no AI)", "sources": ["incident case", "OpenTelemetry telemetry"]}
