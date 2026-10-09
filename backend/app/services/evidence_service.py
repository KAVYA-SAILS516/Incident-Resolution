"""Deterministic evidence for one incident: aggregates, baselines, related errors and a few representative
log lines. This is what the agents see; raw logs are never sent to Gemini in bulk.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from statistics import mean

from app.schemas.incident import Incident
from app.schemas.log import LogEntry
from app.application.knowledge import service_context
from app.application.scenarios import relevant_failure_scenarios
from app.state.store import JsonStore
from app.telemetry import otel
from app.tools.runbook_tools import load_runbook

RELATED_WINDOW_PADDING = timedelta(minutes=2)
MAX_REPRESENTATIVE_LOGS = 10
MAX_TIMELINE_POINTS = 120

_LOG_FIELDS = ("log_id", "timestamp", "level", "service", "host", "method", "endpoint", "status_code",
               "response_time_ms", "error_type", "message", "client_id", "request_id")


def _iso(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _percentile(sorted_values: list[float], pct: float) -> float:
    index = min(len(sorted_values) - 1, max(0, round(pct / 100 * (len(sorted_values) - 1))))
    return sorted_values[index]


def response_time_stats(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "avg": round(mean(ordered), 1),
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "max": ordered[-1],
    }


def log_view(entry: LogEntry) -> dict:
    data = entry.model_dump(include=set(_LOG_FIELDS))
    data["timestamp"] = _iso(entry.timestamp)
    return data


def incident_entries(incident: Incident, store: JsonStore) -> list[LogEntry]:
    return [store.logs_by_id[i] for i in incident.log_ids if i in store.logs_by_id]


def representative_logs(entries: list[LogEntry], limit: int = MAX_REPRESENTATIVE_LOGS) -> list[LogEntry]:
    """First, last, slowest, plus one per host and per distinct (endpoint, status, level) variant."""
    if not entries:
        return []
    picks: dict[str, LogEntry] = {}

    def add(entry: LogEntry) -> None:
        if len(picks) < limit:
            picks.setdefault(entry.log_id, entry)

    add(entries[0])
    add(entries[-1])
    timed = [e for e in entries if e.response_time_ms is not None]
    if timed:
        add(max(timed, key=lambda e: e.response_time_ms))
    seen_variants: set[tuple] = set()
    for entry in entries:
        variant = (entry.endpoint, entry.status_code, entry.level)
        if variant not in seen_variants:
            seen_variants.add(variant)
            add(entry)
    seen_hosts: set[str | None] = {e.host for e in picks.values()}
    for entry in entries:
        if entry.host not in seen_hosts:
            seen_hosts.add(entry.host)
            add(entry)
    return sorted(picks.values(), key=lambda e: e.timestamp)


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


SPIKE_MULTIPLIER = 5
SPIKE_MIN_EVENTS = 10


def spike_minutes(logs: list[LogEntry]) -> tuple[set, float]:
    """Minutes where abnormal logs across ALL services exceed SPIKE_MULTIPLIER x the median minute."""
    minute = lambda e: e.timestamp.replace(second=0, microsecond=0)  # noqa: E731
    per_minute = Counter(minute(e) for e in logs if e.is_abnormal)
    all_minutes = {minute(e) for e in logs}
    if not all_minutes:
        return set(), 0.0
    counts = sorted(per_minute.get(m, 0) for m in all_minutes)
    median = counts[len(counts) // 2]
    threshold = max(SPIKE_MIN_EVENTS, SPIKE_MULTIPLIER * median)
    return {m for m, c in per_minute.items() if c >= threshold}, median


def build_evidence(incident: Incident, store: JsonStore) -> dict:
    entries = incident_entries(incident, store)
    window_start = incident.first_seen - RELATED_WINDOW_PADDING
    window_end = incident.last_seen + RELATED_WINDOW_PADDING
    own_ids = set(incident.log_ids)

    service_logs = [e for e in store.logs if e.service == incident.service]
    window_logs = [e for e in store.logs if window_start <= e.timestamp <= window_end]
    service_window = [e for e in window_logs if e.service == incident.service]
    all_abnormal = sum(1 for e in store.logs if e.is_abnormal)
    window_abnormal = [e for e in window_logs if e.is_abnormal]

    # Response times: the incident's requests vs the same service's normal (INFO, no error) requests.
    baseline_ok = [e.response_time_ms for e in service_logs
                   if not e.is_abnormal and e.response_time_ms is not None]

    per_minute = Counter(_iso(e.timestamp.replace(second=0, microsecond=0)) for e in entries)
    related = Counter((e.service, e.error_type or e.level) for e in window_abnormal if e.log_id not in own_ids)
    overlapping = [
        {"incident_id": i.incident_id, "service": i.service, "error_type": i.error_type, "priority": i.priority}
        for i in store.incidents.values()
        if i.incident_id != incident.incident_id and i.first_seen <= window_end and i.last_seen >= window_start
    ]
    runbook = load_runbook(incident.error_type)

    stats = {
        "occurrences": incident.occurrences,
        "levels": dict(Counter(e.level for e in entries)),
        "status_codes": dict(Counter(str(e.status_code) for e in entries if e.status_code is not None)),
        "hosts": dict(Counter(e.host for e in entries if e.host)),
        "endpoints": dict(Counter(e.endpoint for e in entries if e.endpoint)),
        "methods": dict(Counter(e.method for e in entries if e.method)),
        "distinct_clients": incident.distinct_clients,
        "top_clients": Counter(e.client_id for e in entries if e.client_id).most_common(3),
        "messages": dict(Counter(e.message for e in entries if e.message).most_common(5)),
        "response_time_ms": {
            "incident_requests": response_time_stats([e.response_time_ms for e in entries if e.response_time_ms is not None]),
            "service_normal_requests": response_time_stats(baseline_ok),
        },
    }
    baseline = {
        "service_requests_total": len(service_logs),
        "service_abnormal_rate_overall": _rate(sum(1 for e in service_logs if e.is_abnormal), len(service_logs)),
        "service_requests_in_window": len(service_window),
        "service_abnormal_rate_in_window": _rate(sum(1 for e in service_window if e.is_abnormal), len(service_window)),
        "all_services_abnormal_rate_overall": _rate(all_abnormal, len(store.logs)),
        "all_services_abnormal_rate_in_window": _rate(len(window_abnormal), len(window_logs)),
    }
    spikes, median_per_minute = spike_minutes(store.logs)
    in_spike = sum(1 for e in entries if e.timestamp.replace(second=0, microsecond=0) in spikes)
    spike_assessment = {
        "method": f"a minute is a spike when abnormal logs across all services >= max({SPIKE_MIN_EVENTS}, "
                  f"{SPIKE_MULTIPLIER} x median of {median_per_minute:g}/min)",
        "spike_minutes_in_window": sorted(_iso(m) for m in spikes if window_start <= m <= window_end),
        "incident_events_inside_spike_minutes": in_spike,
        "incident_events_outside_spike_minutes": len(entries) - in_spike,
    }
    related_errors = {
        "window": {"start": _iso(window_start), "end": _iso(window_end)},
        "cross_service_spike": spike_assessment,
        "other_abnormal_logs_in_window": sum(related.values()),
        "services_with_errors_in_window": sorted({e.service for e in window_abnormal}),
        "hosts_with_errors_in_window": sorted({e.host for e in window_abnormal if e.host}),
        "note": "by_service_and_type counts individual log lines; only overlapping_incidents are incidents.",
        "by_service_and_type": [
            {"service": s, "error_type": t, "count": c} for (s, t), c in related.most_common(15)
        ],
        "overlapping_incidents": overlapping[:15],
    }
    reps = representative_logs(entries)

    facts = [
        f"{incident.occurrences} {incident.error_type} events on {incident.service} "
        f"({', '.join(incident.affected_endpoints)}) between {_iso(incident.first_seen)} and {_iso(incident.last_seen)}.",
        f"Status codes: {stats['status_codes'] or 'none'}; levels: {stats['levels']}.",
        f"Hosts affected: {', '.join(incident.hosts) or 'unknown'}; distinct clients: {incident.distinct_clients}.",
        f"Peak {incident.peak_per_minute} events in one minute.",
        f"{incident.service} abnormal-request rate: {baseline['service_abnormal_rate_in_window']:.1%} in the incident window "
        f"vs {baseline['service_abnormal_rate_overall']:.1%} across the whole log.",
        f"All services: {baseline['all_services_abnormal_rate_in_window']:.1%} abnormal in the window vs "
        f"{baseline['all_services_abnormal_rate_overall']:.1%} overall; errors seen on "
        f"{len(related_errors['services_with_errors_in_window'])} service(s) in the window.",
    ]
    if spike_assessment["spike_minutes_in_window"]:
        facts.append(
            f"{in_spike} of {len(entries)} incident events fall inside cross-service spike minutes "
            f"({', '.join(spike_assessment['spike_minutes_in_window'][:6])})."
        )
    else:
        facts.append("No cross-service error spike overlaps this incident; the wider error rate is at its usual level.")
    rt = stats["response_time_ms"]
    if rt["incident_requests"] and rt["service_normal_requests"]:
        facts.append(
            f"Incident request latency p50 {rt['incident_requests']['p50']:g} ms vs normal p50 "
            f"{rt['service_normal_requests']['p50']:g} ms for {incident.service}."
        )
    app_context = service_context(store.application, incident.service)
    if app_context:
        facts.append(
            f"{incident.service} is a {app_context['criticality']} service of {app_context['application']}"
            f" ({app_context['purpose']}); it depends on {', '.join(app_context['depends_on']) or 'no other service'}"
            f" and is used by {', '.join(app_context['depended_on_by']) or 'no other service'}.")
    for link in incident.correlated_incidents[:5]:
        facts.append(f"Correlated: {link['service']} ({link['incident_id']}) failed in {link['shared_traces']} of the same "
                     f"trace(s); relation to {incident.service}: {link['relation']}.")
    related = {incident.service: "the failing service"}
    for dep in (app_context or {}).get("depends_on", []):
        related.setdefault(dep, f"dependency of {incident.service}")
    for link in incident.correlated_incidents:
        related.setdefault(link["service"], f"fails in the same traces ({link['relation']})")
    scenarios = relevant_failure_scenarios(store.application, related)
    traces = [t for t in (otel.fetch_trace(tid) for tid in incident.trace_ids[:2]) if t]
    metrics = otel.service_metrics(incident.service)
    facts.append(f"Runbook for {incident.error_type}: {'available (' + runbook['runbook_id'] + ')' if runbook else 'none'}.")

    return {
        "incident": incident.model_dump(mode="json", exclude={"log_ids", "priority_factors"}),
        "facts": facts,
        "statistics": stats,
        "timeline": {
            "per_minute": [{"minute": m, "count": c} for m, c in sorted(per_minute.items())][:MAX_TIMELINE_POINTS],
            "peak_per_minute": incident.peak_per_minute,
            "duration_seconds": incident.duration_seconds,
        },
        "baseline": baseline,
        "related_errors": related_errors,
        "representative_logs": [log_view(e) for e in reps],
        "application_context": app_context,
        "failure_scenarios": scenarios,
        "correlation": {
            "trace_ids": incident.trace_ids[:10],
            "affected_services": incident.affected_services,
            "correlated_incidents": incident.correlated_incidents,
            "dependency_contexts": [c for c in (service_context(store.application, link["service"])
                                                for link in incident.correlated_incidents[:5]) if c],
        },
        "telemetry": {"traces": traces, "service_metrics": metrics},
        "runbook": {"available": runbook is not None, "runbook_id": runbook["runbook_id"] if runbook else None},
        "data_limits": {
            "log_lines_in_incident": len(entries),
            "log_lines_shared_with_ai": len(reps),
            "not_in_logs": ["deployment/change history", "database server logs"]
            + ([] if metrics else ["service metrics"]) + ([] if traces else ["trace spans"]),
        },
    }
