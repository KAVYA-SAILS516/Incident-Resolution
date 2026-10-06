"""Incident tools for the agents, bound to one incident."""

from __future__ import annotations

from app.schemas.investigation import InvestigationRecord


def make_incident_tools(evidence: dict, investigation: InvestigationRecord | None = None) -> list:
    def get_incident_details() -> dict:
        """The incident as detected by deterministic rules: service, error type, endpoints, time range,
        occurrences, workflow, criticality and priority (already decided; do not change them)."""
        return evidence["incident"]

    tools = [get_incident_details]

    if investigation is not None:
        def get_investigation_result() -> dict:
            """The Investigation Agent's findings for this incident: root cause, confidence, evidence (FACT vs
            AI_INFERENCE), analysis and unknowns."""
            return investigation.model_dump(
                mode="json",
                include={"root_cause", "confidence", "evidence", "analysis", "unknowns", "insufficient_evidence"},
            )

        tools.append(get_investigation_result)
    return tools
