"""Metric-based detection: turn sustained Prometheus anomalies into the normalised event model.

Some failures never appear as WARN/ERROR logs (a service can log its own failures as INFO) but are visible in
the span metrics the OpenTelemetry Collector derives from traces: a high error ratio or a latency jump. For
every evaluation step (30 s by default) where a service breaches a threshold with enough traffic, one event is
produced; the existing detector then needs the usual number of such steps in a row-ish window to open an
incident, so a single blip does not. The events flow through the same grouping, correlation and priority code as
log events, carry the metric evidence in `attributes`, and are labelled source "prometheus".

Anomaly events are deterministic (timestamps are aligned to the step), so re-reading the same period yields the
same events and they de-duplicate.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Callable

from app.config.settings import settings
from app.schemas.log import LogEntry
from app.telemetry.otel import TelemetryUnavailable, _get

SOURCE = "prometheus"
ERROR_RATIO_TYPE = "METRIC_ERROR_RATIO_HIGH"
LATENCY_TYPE = "METRIC_LATENCY_HIGH"

_CALLS = "traces_span_metrics_calls_total"
_BUCKET = "traces_span_metrics_duration_milliseconds_bucket"

RangeQuery = Callable[[str, int, int, int], dict[str, dict[int, float]]]


def prometheus_range(query: str, start: int, end: int, step: int) -> dict[str, dict[int, float]]:
    """{service_name: {unix_ts: value}} for a range query grouped by service_name."""
    if not settings.otel_metrics_url:
        raise TelemetryUnavailable("OTEL_METRICS_URL is not configured")
    reply = _get(f"{settings.otel_metrics_url.rstrip('/')}/api/v1/query_range",
                 params={"query": query, "start": start, "end": end, "step": step}).json()
    series: dict[str, dict[int, float]] = {}
    for row in reply.get("data", {}).get("result", []):
        name = row["metric"].get("service_name")
        if not name:
            continue
        points = {}
        for ts, value in row["values"]:
            try:
                number = float(value)
            except ValueError:
                continue
            if number == number:  # drop NaN (no traffic in the window)
                points[int(float(ts))] = number
        series[name] = points
    return series


def _event(service: str, ts: int, kind: str, message: str, attributes: dict, application: str | None) -> LogEntry:
    when = datetime.fromtimestamp(ts, tz=timezone.utc)
    digest = hashlib.sha1(f"{ts}|{service}|{kind}".encode()).hexdigest()[:12]
    return LogEntry(log_id=f"{SOURCE}:{digest}", source=SOURCE, line_number=0, timestamp=when, level="ERROR", service=service,
                    error_type=kind, message=message, raw_message=message, application=application,
                    attributes={"source.label": "Prometheus Metrics", **attributes})


def detect_metric_events(application: str | None, minutes: int | None = None, *, query: RangeQuery = prometheus_range,
                         now: float | None = None) -> list[LogEntry]:
    """Anomaly events for the last `minutes`. Raises TelemetryUnavailable when Prometheus cannot be read."""
    if not settings.otel_enabled:
        raise TelemetryUnavailable("OpenTelemetry integration is disabled (OTEL_ENABLED=false)")
    if not settings.metric_detection:
        return []
    step, window = settings.metric_step_seconds, settings.metric_window_seconds
    end = int((now if now is not None else time.time()) // step) * step  # aligned: repeated reads give identical events
    start = end - (minutes or settings.otel_lookback_minutes) * 60
    requests = query(f"sum by (service_name)(increase({_CALLS}[{window}s]))", start, end, step)
    errors = query(f'sum by (service_name)(increase({_CALLS}{{status_code="STATUS_CODE_ERROR"}}[{window}s]))', start, end, step)
    p95 = query(f'histogram_quantile(0.95, sum by (le, service_name)(rate({_BUCKET}{{span_kind="SPAN_KIND_SERVER"}}[{window}s])))',
                start, end, step)
    base = query(f'histogram_quantile(0.95, sum by (le, service_name)(rate({_BUCKET}{{span_kind="SPAN_KIND_SERVER"}}[30m])))',
                 start, end, step)
    events: list[LogEntry] = []
    for service, reqs in requests.items():
        for ts, count in reqs.items():
            if count < settings.metric_min_requests:
                continue
            failed = errors.get(service, {}).get(ts, 0.0)
            ratio = failed / count
            if ratio >= settings.metric_error_ratio_min:
                events.append(_event(
                    service, ts, ERROR_RATIO_TYPE,
                    f"{service} error ratio {ratio:.0%} ({failed:.0f} of {count:.0f} spans failed in {window}s)",
                    {"metric.name": "span error ratio", "metric.value": round(ratio, 4), "metric.window_seconds": window,
                     "metric.requests": round(count, 1), "metric.errors": round(failed, 1)}, application))
            latency, baseline = p95.get(service, {}).get(ts), base.get(service, {}).get(ts)
            if (latency is not None and baseline is not None and latency >= settings.metric_latency_min_ms
                    and latency >= settings.metric_latency_factor * baseline):
                events.append(_event(
                    service, ts, LATENCY_TYPE,
                    f"{service} p95 latency {latency:.0f} ms, {latency / max(baseline, 1):.1f}x its 30-minute baseline ({baseline:.0f} ms)",
                    {"metric.name": "p95 latency ms", "metric.value": round(latency, 1), "metric.baseline": round(baseline, 1),
                     "metric.window_seconds": window, "metric.requests": round(count, 1)}, application))
    return sorted(events, key=lambda e: e.timestamp)
