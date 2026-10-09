"""Trace-based correlation of detected incidents.

Detection groups logs per (service, error, endpoint). Correlation then uses OpenTelemetry context: logs of
different services that carry the same trace_id belong to one request, so a checkout failure whose traces
also contain payment failures is one correlated event. Dependency relationships from the scanned
application say which side is upstream (caller) and which is the dependency.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Callable

from app.application.models import ApplicationKnowledge
from app.schemas.incident import Incident
from app.schemas.log import LogEntry

MAX_TRACE_IDS = 50
TRACE_LOOKUPS_PER_INCIDENT = 5  # sample traces asked from the trace backend per incident


def _relation(knowledge: ApplicationKnowledge | None, service: str, other: str) -> str:
    info = knowledge.service(service) if knowledge else None
    if info is None:
        return "same_trace"
    if other in info.dependencies:
        return "dependency"  # `other` is called by `service`
    if other in info.dependents:
        return "dependent"  # `other` calls `service`
    return "same_trace"


def _overlap_in_time(a: Incident, b: Incident, slack: timedelta = timedelta(minutes=2)) -> bool:
    return a.first_seen - slack <= b.last_seen and b.first_seen - slack <= a.last_seen


def apply_application_context(incident: Incident, knowledge: ApplicationKnowledge | None,
                              entries: list[LogEntry]) -> None:
    info = knowledge.service(incident.service) if knowledge else None
    observed = sorted({e.application for e in entries if e.application})
    incident.application = knowledge.application_name if info else (observed[0] if observed else None)
    incident.service_criticality = info.criticality if info else None


def correlate(incidents: list[Incident], entries_by_id: dict[str, LogEntry], all_entries: list[LogEntry],
              knowledge: ApplicationKnowledge | None,
              trace_lookup: Callable[[str], dict | None] | None = None) -> None:
    """Fill trace_ids, affected_services and correlated_incidents on each incident (in place).

    Logs only show services that log their failures. `trace_lookup` (the trace backend) additionally shows the
    services whose spans failed inside the same traces, e.g. a caller that reports nothing in its own logs."""
    abnormal_by_trace: dict[str, set[str]] = defaultdict(set)
    for entry in all_entries:
        if entry.trace_id and entry.is_abnormal:
            abnormal_by_trace[entry.trace_id].add(entry.service)

    traces_of: dict[str, set[str]] = {}
    for incident in incidents:
        traces = {entries_by_id[i].trace_id for i in incident.log_ids if i in entries_by_id and entries_by_id[i].trace_id}
        traces_of[incident.incident_id] = traces
        services = {incident.service}
        for trace in traces:
            services |= abnormal_by_trace.get(trace, set())
        if trace_lookup:
            for trace in sorted(traces)[:TRACE_LOOKUPS_PER_INCIDENT]:
                summary = trace_lookup(trace)
                services |= set((summary or {}).get("error_services", []))
        incident.trace_ids = sorted(traces)[:MAX_TRACE_IDS]
        incident.affected_services = sorted(services)

    for incident in incidents:
        correlated = []
        for other in incidents:
            if other.incident_id == incident.incident_id or other.service == incident.service:
                continue
            shared = traces_of[incident.incident_id] & traces_of[other.incident_id]
            # Also correlate through traces: other's service failing inside this incident's traces.
            via_traces = {t for t in traces_of[incident.incident_id] if other.service in abnormal_by_trace.get(t, ())}
            overlap = shared | via_traces
            relation = _relation(knowledge, incident.service, other.service)
            if overlap:
                correlated.append({"incident_id": other.incident_id, "service": other.service,
                                   "shared_traces": len(overlap), "relation": relation, "basis": "trace"})
            elif relation in ("dependency", "dependent") and _overlap_in_time(incident, other):
                # No shared trace ids (e.g. both detected from metrics): services that call each other and fail at
                # the same time are correlated through the application's dependency graph.
                correlated.append({"incident_id": other.incident_id, "service": other.service, "shared_traces": 0,
                                   "relation": relation, "basis": "time+dependency"})
                if other.service not in incident.affected_services:
                    incident.affected_services = sorted({*incident.affected_services, other.service})
        incident.correlated_incidents = sorted(correlated, key=lambda c: -c["shared_traces"])
