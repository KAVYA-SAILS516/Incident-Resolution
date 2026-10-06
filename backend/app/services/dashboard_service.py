"""Dashboard aggregation: the counts behind `GET /api/dashboard/summary`, extracted so the chat agent's
`get_dashboard_overview` tool (app/tools/chat_tools.py) can reuse the exact same numbers instead of
recomputing them.
"""

from __future__ import annotations

from collections import Counter

from app.agents.auto_analysis import status_for
from app.api.incidents import incident_view
from app.config.settings import settings
from app.state.store import JsonStore

TIMELINE_BUCKET_MINUTES = 5

# Metrics this POC has no backing data for (no remediation, approval, takeover or verification exists at
# all). Reported explicitly as not enabled, rather than omitted or invented.
NOT_ENABLED = {
    "mttd": "No end-to-end detection-to-diagnosis timestamp is tracked in this POC.",
    "mttr": "Not enabled in this POC: there is no remediation step.",
    "resolution_rate": 'Not enabled in this POC: there is no "resolved" state.',
    "auto_remediation_count": "Not enabled in this POC: there is no remediation step.",
    "human_approval_count": "Not enabled in this POC: there is no approval workflow.",
    "human_takeover_count": "Not enabled in this POC: there is no takeover workflow.",
    "verification_outcomes": "Not enabled in this POC: there is no verification step.",
}


def summarize(store: JsonStore) -> dict:
    incidents = sorted(store.incidents.values(), key=lambda i: (-i.priority_score, i.first_seen))
    count = lambda attr: dict(Counter(getattr(i, attr) for i in incidents).most_common())  # noqa: E731
    # The real, backend-persisted workflow stage of every incident (never the UI's own state).
    stage = Counter(status_for(store, i.incident_id).state for i in incidents)

    buckets: dict[str, Counter] = {}
    for entry in store.logs:
        ts = entry.timestamp
        bucket = ts.replace(minute=ts.minute - ts.minute % TIMELINE_BUCKET_MINUTES, second=0, microsecond=0)
        counter = buckets.setdefault(bucket.isoformat().replace("+00:00", "Z"), Counter())
        counter["requests"] += 1
        if entry.level in ("ERROR", "CRITICAL"):
            counter["errors"] += 1
        elif entry.level == "WARNING":
            counter["warnings"] += 1

    return {
        "ingested": store.summary is not None,
        "ingest": store.summary.model_dump(mode="json") if store.summary else None,
        "incidents": {
            "total": len(incidents),
            "by_priority": {p: sum(1 for i in incidents if i.priority == p) for p in ("P1", "P2", "P3", "P4")},
            "by_workflow": count("workflow"),
            "by_criticality": count("workflow_criticality"),
            "by_service": count("service"),
            "by_error_type": count("error_type"),
            "by_status": count("status"),
        },
        "agents": {
            "investigated": len(store.investigations),
            "recommendations_ready": len(store.recommendations),
            "llm_configured": settings.llm_configured,
            "auto_analyze": settings.auto_analyze,
            "model": settings.model,
            "queue": {"queued": stage.get("queued", 0), "investigating": stage.get("investigating", 0),
                      "recommending": stage.get("recommending", 0), "failed": stage.get("failed", 0)},
        },
        "top_incidents": [incident_view(i, store) for i in incidents[:5]],
        "timeline": {
            "bucket_minutes": TIMELINE_BUCKET_MINUTES,
            "points": [{"time": t, **c} for t, c in sorted(buckets.items())],
        },
        "analytics": {
            "incident_volume": len(incidents),
            "priority_distribution": {p: sum(1 for i in incidents if i.priority == p) for p in ("P1", "P2", "P3", "P4")},
            "workflow_stage_distribution": {
                "queued": stage.get("queued", 0), "investigating": stage.get("investigating", 0),
                "recommending": stage.get("recommending", 0), "done": stage.get("done", 0),
                "failed": stage.get("failed", 0), "disabled": stage.get("disabled", 0),
            },
            **{name: {"available": False, "note": note} for name, note in NOT_ENABLED.items()},
        },
    }
