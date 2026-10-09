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



def _avg_seconds(values: list[float]) -> dict:
    if not values:
        return {"available": False, "note": "No data yet."}
    return {"available": True, "value": round(sum(values) / len(values), 1), "unit": "seconds", "samples": len(values)}


def resolution_analytics(store: JsonStore, incidents: list) -> dict:
    """Real counts from the persisted resolution records (approvals, takeovers, verification outcomes, MTTD, MTTR)."""
    records = [store.resolutions[i.incident_id] for i in incidents if i.incident_id in store.resolutions]
    attempts = [a for r in records for a in r.attempts]
    resolved = [(i, store.resolutions[i.incident_id]) for i in incidents
                if i.incident_id in store.resolutions and store.resolutions[i.incident_id].state == "resolved"]
    mttr = [(max(v.checked_at for v in r.verifications if v.status == "SUCCESS") - i.first_seen).total_seconds()
            for i, r in resolved if any(v.status == "SUCCESS" for v in r.verifications)]
    mttd = [(i.detected_at - i.first_seen).total_seconds() for i in incidents if i.detected_at]
    outcomes = Counter(v.status for r in records for v in r.verifications)
    count = lambda n, note: {"available": True, "value": n, "note": note}  # noqa: E731
    return {
        "mttd": _avg_seconds(mttd) | {"note": "Average from the first failing event to detection by this platform."},
        "mttr": _avg_seconds(mttr) | {"note": "Average from the first failing event to verified recovery."},
        "resolution_rate": count(round(len(resolved) / len(incidents), 3) if incidents else 0.0, "Share of incidents with verified recovery."),
        "auto_remediation_count": count(sum(1 for a in attempts if a.approved_by == "policy (auto)"), "Actions run under policy without approval."),
        "human_approval_count": count(sum(1 for a in attempts if a.approved_by != "policy (auto)"), "Actions run after a person approved them."),
        "human_takeover_count": count(sum(1 for r in records if r.state == "human_takeover"), "Incidents a person took over."),
        "verification_outcomes": count(dict(outcomes), "Telemetry verification results by status."),
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
        "application": {"name": store.application.application_name if store.application else None,
                          "scanned": store.application is not None, "observability": "OpenTelemetry"},
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
            **resolution_analytics(store, incidents),
        },
    }
