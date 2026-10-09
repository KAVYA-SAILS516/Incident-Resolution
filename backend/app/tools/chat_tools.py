"""Chat Agent tools: read-only, dashboard-wide access to the same deterministic data the rest of the UI
shows. No raw logs and no other incident's full evidence payload - only compact summaries, so a chat turn
stays a small, bounded request.
"""

from __future__ import annotations

from app.application.knowledge import answer_question
from app.schemas.incident import Incident
from app.services.dashboard_service import summarize
from app.services.evidence_service import build_evidence
from app.state.store import JsonStore

_INCIDENT_FIELDS = (
    "incident_id", "service", "environment", "error_type", "status_code", "workflow", "workflow_criticality",
    "priority", "priority_score", "status", "occurrences", "first_seen", "last_seen",
)


def _compact(incident: Incident) -> dict:
    return incident.model_dump(mode="json", include=set(_INCIDENT_FIELDS))


def make_chat_tools(store: JsonStore) -> list:
    def list_incidents(priority: str | None = None, status: str | None = None) -> list[dict]:
        """List incidents, highest priority first. Optional filters: priority ("P1".."P4") and status
        ("open", "investigated", "recommendations_ready"). Returns compact rows, not full evidence."""
        items = sorted(store.incidents.values(), key=lambda i: (-i.priority_score, i.first_seen))
        if priority:
            items = [i for i in items if i.priority == priority.upper()]
        if status:
            items = [i for i in items if i.status == status]
        return [_compact(i) for i in items]

    def get_incident_summary(incident_id: str) -> dict:
        """One incident's core facts, investigation root cause (if done) and top recommendation (if any).
        Use this before answering a question about a specific incident."""
        incident = store.incidents.get(incident_id)
        if incident is None:
            return {"error": f"No incident with id {incident_id!r}"}
        result: dict = {**_compact(incident), "facts": build_evidence(incident, store)["facts"],
                        "application": incident.application, "service_criticality": incident.service_criticality,
                        "priority_reason": incident.priority_reason, "affected_services": incident.affected_services}
        investigation = store.investigations.get(incident_id)
        if investigation is not None:
            result["investigation"] = {
                "root_cause": investigation.root_cause,
                "confidence": investigation.confidence,
                "insufficient_evidence": investigation.insufficient_evidence,
            }
        resolution = store.resolutions.get(incident_id)
        if resolution is not None:
            result["resolution"] = {"decision": resolution.decision, "state": resolution.state,
                                    "recommended_option_id": resolution.recommended_option_id,
                                    "reason": resolution.decision_reason, "failed_options": resolution.failed_options}
        recommendations = store.recommendations.get(incident_id)
        if recommendations is not None and recommendations.recommendations:
            top = recommendations.recommendations[0]
            result["top_recommendation"] = {"title": top.title, "risk": top.risk, "source": top.source}
        return result

    def get_dashboard_overview() -> dict:
        """Aggregate counts across all incidents: totals by priority/workflow/status, and how many are
        queued, investigating, recommending or failed in the background analysis workflow right now."""
        summary = summarize(store)
        return {"incidents": summary["incidents"], "queue": summary["agents"]["queue"]}

    def get_application_knowledge(question: str) -> dict:
        """Answer a question about the scanned application (its services, what each does, dependencies, APIs,
        criticality, telemetry, documented failure scenarios) from the application scan. Returns the answer
        and supporting facts; says so when no application has been scanned."""
        return answer_question(store.application, question)

    return [list_incidents, get_incident_summary, get_dashboard_overview, get_application_knowledge]
