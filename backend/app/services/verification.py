"""Verification: did the incident actually recover? Judged from OpenTelemetry events and metrics that
arrived after the remediation - never from the fact that the action ran.

SUCCESS      the share of abnormal events among the service's events fell to a small fraction of what it was
             during the incident, with enough traffic after the action to tell.
FAILED       it did not.
UNKNOWN too early, no telemetry after the action, or too little traffic through the service (silence is
             not recovery).
On FAILED the failed option is excluded and the resolution is re-decided; after the maximum number of failed
attempts the decision becomes HUMAN_TAKEOVER.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.application.scenarios import relevant_failure_scenarios
from app.config import resolution_rules as rules
from app.config.settings import settings
from app.schemas.log import LogEntry
from app.schemas.resolution import RemediationAttempt, ResolutionRecord, VerificationResult
from app.services.resolution_service import refresh
from app.state.store import JsonStore
from app.telemetry import otel

METRICS_OK_ERROR_RATIO = 0.05


class VerificationNotReady(Exception):
    """There is nothing to verify (no executed, unverified action)."""


def _pending_attempt(record: ResolutionRecord) -> RemediationAttempt | None:
    verified = {v.attempt_id for v in record.verifications}
    return next((a for a in reversed(record.attempts) if a.status == "executed" and a.attempt_id not in verified), None)


def _refresh_telemetry(store: JsonStore, application: str | None,
                       pull: Callable[..., list[LogEntry]]) -> tuple[list[LogEntry], str | None]:
    note = None
    try:
        fresh = pull(application, settings.otel_lookback_minutes)
        otel.save_events(store.data_dir, fresh)
    except otel.TelemetryUnavailable as exc:
        note = f"telemetry backend not read: {exc}"
    return otel.load_events(store.data_dir), note


def _conclude(store: JsonStore, record: ResolutionRecord, incident_id: str, attempt: RemediationAttempt,
              verification: VerificationResult, recovered: bool, now: datetime) -> VerificationResult:
    record.verifications.append(verification)
    record.updated_at = now
    if recovered:
        record.state = "resolved"
        record.next_step = "Resolved: recovery was verified from telemetry."
        store.save_resolution(record)
    else:
        record.failed_options.append(attempt.option_id)
        record.state = "proposed"
        store.save_resolution(record)
        refresh(store, incident_id)  # re-decide without the failed option (or hand over to a person)
    return verification


def verify(store: JsonStore, incident_id: str, *, now: datetime | None = None,
           pull: Callable[..., list[LogEntry]] = otel.fetch_logs,
           metrics: Callable[[str, int], dict | None] = otel.service_metrics,
           p95: Callable[[str, int], float | None] = otel.service_p95_ms) -> VerificationResult:
    record = store.resolutions.get(incident_id)
    incident = store.incidents.get(incident_id)
    attempt = _pending_attempt(record) if record else None
    if record is None or incident is None or attempt is None:
        raise VerificationNotReady("There is no executed action waiting for verification")
    now = now or datetime.now(timezone.utc)
    elapsed = (now - attempt.executed_at).total_seconds()
    window = [e for e in store.logs if e.service == incident.service and incident.first_seen <= e.timestamp <= incident.last_seen]
    before_ratio = incident.occurrences / max(len(window), incident.occurrences, 1)
    before = {"service": incident.service, "abnormal_events": incident.occurrences, "events": max(len(window), incident.occurrences),
              "abnormal_ratio": round(before_ratio, 3)}

    def result(status: str, reason: str, after: dict | None = None) -> VerificationResult:
        return VerificationResult(attempt_id=attempt.attempt_id, status=status, reason=reason, before=before,
                                  after=after or {}, checked_at=now)

    if elapsed < settings.verification_wait_seconds:
        return result("UNKNOWN", f"Too early: wait {int(settings.verification_wait_seconds - elapsed)}s more "
                                      "so enough telemetry arrives after the action.")

    events, note = _refresh_telemetry(store, incident.application, pull)
    after_events = [e for e in events if e.timestamp >= attempt.executed_at]
    if not after_events:
        return result("UNKNOWN", "No telemetry arrived after the action, so recovery cannot be judged"
                                      + (f" ({note})." if note else "."))
    if incident.error_type.startswith("METRIC_"):  # opened from Prometheus metrics: verify against the same metrics
        return _verify_metric_incident(store, record, incident, attempt, result, elapsed, now, metrics, p95)
    service_events = [e for e in after_events if e.service == incident.service]
    abnormal = [e for e in service_events if e.is_abnormal]
    ratio = len(abnormal) / len(service_events) if service_events else 0.0
    after = {"service": incident.service, "abnormal_events": len(abnormal), "events": len(service_events),
             "abnormal_ratio": round(ratio, 3), "events_observed_all_services": len(after_events)}
    measured = metrics(incident.service, int(elapsed)) if settings.otel_enabled else None
    if measured is not None:
        after["metrics"] = measured
    # Configuration evidence about the relevant failure scenarios, read now (context only: telemetry decides).
    scenario_state = {s["scenario"]: s["enabled"]
                      for s in relevant_failure_scenarios(store.application, {incident.service: "the failing service"})}
    if scenario_state:
        after["scenario_state"] = scenario_state
    if len(service_events) < rules.MIN_TRAFFIC_AFTER:
        return result("UNKNOWN", f"Only {len(service_events)} {incident.service} event(s) since the action - too little "
                                      "traffic to judge recovery. Verify again once the service has handled more requests.", after)

    recovered = ratio <= rules.RECOVERY_RATIO * before_ratio
    if recovered and measured is not None and measured["error_ratio"] > METRICS_OK_ERROR_RATIO:
        recovered = False
    still_on = [name for name, on in scenario_state.items() if on is True]
    reason = (f"{incident.service} abnormal share {before['abnormal_ratio']:.0%} during the incident, {ratio:.0%} after "
              f"({len(abnormal)} of {len(service_events)} events since the action)"
              + (f"; failure scenario still enabled in configuration: {', '.join(still_on)}" if still_on else ""))
    verification = result("SUCCESS" if recovered else "FAILED", reason + (" - recovered." if recovered else " - not recovered."),
                          after)
    return _conclude(store, record, incident_id, attempt, verification, recovered, now)


def _verify_metric_incident(store, record, incident, attempt, result, elapsed, now, metrics, p95) -> VerificationResult:
    """Recovery of an incident detected from metrics: the same Prometheus signal, measured since the action."""
    entries = [store.logs_by_id[i] for i in incident.log_ids if i in store.logs_by_id]
    peak = max((e.attributes.get("metric.value", 0.0) for e in entries), default=0.0)
    latency_incident = incident.error_type == "METRIC_LATENCY_HIGH"
    baseline = max((e.attributes.get("metric.baseline", 0.0) for e in entries), default=0.0)
    before = {"service": incident.service, "metric": "p95 latency ms" if latency_incident else "error ratio", "peak": peak}
    window = max(30, int(elapsed))
    measured = metrics(incident.service, window)
    if measured is None:
        return VerificationResult(attempt_id=attempt.attempt_id, status="UNKNOWN", before=before, checked_at=now,
                                  reason="Prometheus metrics could not be read, so recovery cannot be judged.")
    after = {"service": incident.service, "metrics": measured, "window_seconds": window}
    if measured["requests"] < rules.MIN_TRAFFIC_AFTER:
        return VerificationResult(attempt_id=attempt.attempt_id, status="UNKNOWN", before=before, after=after, checked_at=now,
                                  reason=f"Only {measured['requests']:g} {incident.service} request(s) since the action - too little traffic "
                                         "to judge recovery. Verify again once the service has handled more requests.")
    if latency_incident:
        latency = p95(incident.service, window)
        after["p95_ms"] = latency
        if latency is None:
            return VerificationResult(attempt_id=attempt.attempt_id, status="UNKNOWN", before=before, after=after, checked_at=now,
                                      reason="p95 latency could not be read, so recovery cannot be judged.")
        recovered = latency < settings.metric_latency_min_ms or latency < settings.metric_latency_factor * max(baseline, 1.0)
        reason = f"{incident.service} p95 latency peaked at {peak:.0f} ms during the incident, {latency:.0f} ms since the action"
    else:
        recovered = measured["error_ratio"] <= rules.RECOVERY_RATIO * peak or measured["error_ratio"] < settings.metric_error_ratio_min
        reason = (f"{incident.service} error ratio peaked at {peak:.0%} during the incident, {measured['error_ratio']:.0%} since the "
                  f"action ({measured['errors']:g} of {measured['requests']:g} spans)")
    verification = VerificationResult(attempt_id=attempt.attempt_id, status="SUCCESS" if recovered else "FAILED", before=before,
                                      after=after, checked_at=now,
                                      reason=reason + (" - recovered." if recovered else " - not recovered."))
    return _conclude(store, record, incident.incident_id, attempt, verification, recovered, now)
