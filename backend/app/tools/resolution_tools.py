"""Resolution tool for the Resolution Decision Agent: the ONLY remediation options it may choose from. They are
produced by the policy layer (registered actions, services the application knows, failed options excluded)."""

from __future__ import annotations

from app.schemas.incident import Incident
from app.schemas.investigation import InvestigationRecord
from app.services import resolution_service
from app.state.store import JsonStore

_OPTION_FIELDS = {"option_id", "title", "description", "prerequisites", "expected_outcome", "risk", "impact", "blast_radius", "reversibility", "confidence", "execution_mode"}


def make_resolution_tools(store: JsonStore, incident: Incident, investigation: InvestigationRecord | None) -> list:
    def get_resolution_candidates() -> dict:
        """The remediation options that policy allows for this incident, with risk, impact, blast radius,
        reversibility, confidence and the execution mode policy assigns. Also the options that already failed
        verification (with the telemetry reason) - never choose those. Choose recommended_option_id ONLY from
        these option_id values; there are no other actions."""
        record = store.resolutions.get(incident.incident_id)
        failed = set(record.failed_options) if record else set()
        options = resolution_service.build_options(incident, investigation, store.application, failed)
        attempts = {a.attempt_id: a for a in record.attempts} if record else {}
        failures = [{"option_id": attempts[v.attempt_id].option_id, "verification": v.reason}
                    for v in (record.verifications if record else []) if v.status == "FAILED" and v.attempt_id in attempts]
        return {
            "candidates": [o.model_dump(include=_OPTION_FIELDS) for o in options],
            "previously_failed": failures,
            "policy": "The system, not the model, decides what may run: execution_mode is fixed by policy and a "
                      "person approves anything that is not AUTO_EXECUTE. Verification from telemetry, not you, "
                      "decides whether an incident is resolved.",
        }

    return [get_resolution_candidates]
