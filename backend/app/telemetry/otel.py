"""OpenTelemetry runtime adapter.

The application's own instrumentation sends logs / metrics / traces to its OpenTelemetry Collector, which
exports them to a telemetry backend. This module reads from that backend and converts what it finds into
the existing LogEntry event model, so the existing detector / grouping / priority engine apply unchanged.

Nothing is fabricated: when an endpoint is not configured or unreachable the signal is reported as
unavailable (TelemetryUnavailable) rather than replaced with sample data.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config.settings import settings
from app.schemas.log import LogEntry

SOURCE = "opentelemetry"
_SEVERITY_TEXT = {"TRACE": "DEBUG", "DEBUG": "DEBUG", "INFO": "INFO", "NOTICE": "INFO", "WARN": "WARNING",
                  "WARNING": "WARNING", "ERROR": "ERROR", "ERR": "ERROR", "FATAL": "CRITICAL", "CRITICAL": "CRITICAL"}
_NOISE = re.compile(r"\b(?:[0-9a-f]{8,}|\d+)\b", re.I)
MAX_STORED_EVENTS = 50_000
GENERIC_ERROR_TYPES = {"error", "exception", "runtimeerror", "unknown"}
PULL_PAGE_SIZE = 5000
MAX_PULL_EVENTS = 20_000


class TelemetryUnavailable(RuntimeError):
    """A telemetry signal cannot be read (not configured, unreachable, or an unexpected reply)."""


# --- parsing ---------------------------------------------------------------------------------------

def _any_value(value):
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            return int(value[key]) if key == "intValue" else value[key]
    if "kvlistValue" in value:
        return {kv.get("key"): _any_value(kv.get("value")) for kv in value["kvlistValue"].get("values", [])}
    if "arrayValue" in value:
        return [_any_value(v) for v in value["arrayValue"].get("values", [])]
    return None


def _attrs(raw) -> dict:
    """Attributes as a flat dict, from either OTLP `[{key, value}]` lists or plain objects."""
    if isinstance(raw, list):
        return {item.get("key"): _any_value(item.get("value")) for item in raw if isinstance(item, dict)}
    return dict(raw) if isinstance(raw, dict) else {}


def _flatten(data: dict, prefix: str = "") -> dict:
    """{"service": {"name": "x"}} -> {"service.name": "x"}; already-dotted keys pass through."""
    flat = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def _lookup(doc: dict, *paths: tuple[str, ...]):
    """First value found along any of the paths. Each path step may be a nested key or a dotted flat key."""
    for path in paths:
        node = doc
        for step in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(step)
        if node not in (None, ""):
            return node
    return None


def _timestamp(value) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            number = int(value)
            seconds = number / 1e9 if number > 1e17 else number / 1e6 if number > 1e14 else number / 1e3 if number > 1e11 else number
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _level(text, number) -> str:
    if text:
        mapped = _SEVERITY_TEXT.get(str(text).strip().upper())
        if mapped:
            return mapped
    try:
        n = int(number)
    except (TypeError, ValueError):
        return "INFO"
    return "DEBUG" if n <= 8 else "INFO" if n <= 12 else "WARNING" if n <= 16 else "ERROR" if n <= 20 else "CRITICAL"


def message_signature(message: str | None) -> str:
    """A stable error label from a message: first clause, ids/numbers removed. Groups repeats of one failure."""
    if not message:
        return "UNKNOWN_ERROR"
    first = re.split(r"[.:;\n{(]", message.strip(), maxsplit=1)[0]
    words = re.sub(r"[^A-Za-z ]+", " ", _NOISE.sub(" ", first)).upper().split()[:6]
    return "_".join(words) or "UNKNOWN_ERROR"


def build_entry(*, timestamp: datetime | None, service: str | None, level: str, body, trace_id: str | None,
                span_id: str | None, resource: dict, attributes: dict, application: str | None) -> LogEntry | None:
    if timestamp is None or not service:
        return None
    message = body if isinstance(body, str) else json.dumps(body, default=str) if body is not None else None
    if message and message.lstrip().startswith("{"):  # structured (JSON) log bodies, e.g. pino
        try:
            parsed = json.loads(message)
            message = parsed.get("msg") or parsed.get("message") or message
            level = _SEVERITY_TEXT.get(str(parsed.get("level", "")).upper(), level)
        except ValueError:
            pass
    status = attributes.get("http.response.status_code", attributes.get("http.status_code"))
    path = attributes.get("url.path") or attributes.get("http.target") or attributes.get("http.route")
    rpc = (f"{attributes['rpc.service']}/{attributes['rpc.method']}"
           if attributes.get("rpc.service") and attributes.get("rpc.method") else None)
    error_type = attributes.get("exception.type") or attributes.get("error.type")
    if (not error_type or str(error_type).lower() in GENERIC_ERROR_TYPES) and level in ("WARNING", "ERROR", "CRITICAL"):
        error_type = message_signature(message)  # "Error" alone says nothing; the message names the failure
    digest = hashlib.sha1(f"{timestamp.isoformat()}|{service}|{message}|{trace_id}|{span_id}".encode()).hexdigest()[:12]
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    return LogEntry(
        log_id=f"{SOURCE}:{digest}", source=SOURCE, line_number=0, timestamp=timestamp, level=level,
        service=str(service),
        environment=resource.get("deployment.environment.name") or resource.get("deployment.environment")
        or resource.get("service.namespace"),
        host=resource.get("host.name") or resource.get("container.id") or resource.get("service.instance.id"),
        request_id=trace_id, method=attributes.get("http.request.method") or attributes.get("http.method"),
        endpoint=path or rpc, status_code=status_code,
        error_type=str(error_type).upper() if error_type else None, message=message,
        raw_message=message or "", application=application or resource.get("service.namespace"),
        trace_id=trace_id or None, span_id=span_id or None,
        attributes={**{f"resource.{k}": v for k, v in resource.items() if k.startswith("service.")}, **attributes},
    )


def parse_otlp_logs(payload: dict, application: str | None = None) -> list[LogEntry]:
    """OTLP/JSON `ExportLogsServiceRequest` (what an OTLP/HTTP exporter sends) -> LogEntry list."""
    entries = []
    for resource_logs in payload.get("resourceLogs", []):
        resource = _attrs(resource_logs.get("resource", {}).get("attributes"))
        for scope in resource_logs.get("scopeLogs", []):
            for record in scope.get("logRecords", []):
                entry = build_entry(
                    timestamp=_timestamp(record.get("timeUnixNano") or record.get("observedTimeUnixNano")),
                    service=resource.get("service.name"), level=_level(record.get("severityText"), record.get("severityNumber")),
                    body=_any_value(record.get("body")), trace_id=record.get("traceId") or None,
                    span_id=record.get("spanId") or None, resource=resource, attributes=_attrs(record.get("attributes")),
                    application=application)
                if entry:
                    entries.append(entry)
    return entries


def parse_search_hit(hit: dict, application: str | None = None) -> LogEntry | None:
    """One OpenSearch document written by the Collector's opensearch exporter (SS4O or flat mapping)."""
    doc = hit.get("_source", hit)
    raw = doc.get("resource") or {}
    resource = _flatten(_attrs(raw.get("attributes")) if isinstance(raw, dict) and "attributes" in raw else raw)
    attributes = _flatten(_attrs(doc.get("attributes")))
    service = resource.get("service.name") or doc.get("serviceName") or doc.get("service.name")
    resource_flat = {**resource, "service.name": service}
    return build_entry(
        timestamp=_timestamp(_lookup(doc, ("@timestamp",), ("time",), ("timestamp",), ("observedTimestamp",))),
        service=service, level=_level(_lookup(doc, ("severityText",), ("severity", "text")),
                                      _lookup(doc, ("severityNumber",), ("severity", "number"))),
        body=_lookup(doc, ("body",), ("message",)), trace_id=_lookup(doc, ("traceId",), ("trace_id",)),
        span_id=_lookup(doc, ("spanId",), ("span_id",)), resource=resource_flat, attributes=attributes,
        application=application)


# --- persistence of pulled events ------------------------------------------------------------------

def events_path(data_dir: Path) -> Path:
    return data_dir / "telemetry" / "otel_logs.json"


def load_events(data_dir: Path) -> list[LogEntry]:
    path = events_path(data_dir)
    if not path.exists():
        return []
    return [LogEntry.model_validate(e) for e in json.loads(path.read_text(encoding="utf-8"))]


def save_events(data_dir: Path, new_entries: list[LogEntry]) -> list[LogEntry]:
    """Merge into the stored telemetry events (deduplicated by log_id, newest MAX_STORED_EVENTS kept)."""
    merged = {e.log_id: e for e in load_events(data_dir)}
    merged.update({e.log_id: e for e in new_entries})
    entries = sorted(merged.values(), key=lambda e: e.timestamp)[-MAX_STORED_EVENTS:]
    path = events_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([e.model_dump(mode="json") for e in entries]), encoding="utf-8")
    tmp.replace(path)
    return entries


# --- reading the telemetry backend -----------------------------------------------------------------

def _get(url: str, **kwargs) -> httpx.Response:
    try:
        response = httpx.request(kwargs.pop("method", "GET"), url, timeout=settings.otel_timeout_seconds, **kwargs)
        response.raise_for_status()
        return response
    except httpx.HTTPError as exc:
        raise TelemetryUnavailable(f"{url}: {exc}") from exc


def status() -> dict:
    return {
        "enabled": settings.otel_enabled,
        "collector_endpoint": settings.otel_endpoint,
        "logs": settings.otel_logs_url is not None,
        "traces": settings.otel_traces_url is not None,
        "metrics": settings.otel_metrics_url is not None,
    }


def fetch_logs(application: str | None, since_minutes: int | None = None) -> list[LogEntry]:
    if not settings.otel_enabled:
        raise TelemetryUnavailable("OpenTelemetry integration is disabled (OTEL_ENABLED=false)")
    if not settings.otel_logs_url:
        raise TelemetryUnavailable("OTEL_LOGS_URL is not configured")
    minutes = since_minutes or settings.otel_lookback_minutes
    url = f"{settings.otel_logs_url.rstrip('/')}/{settings.otel_logs_index}/_search"
    hits: list[dict] = []
    search_after = None
    while len(hits) < MAX_PULL_EVENTS:  # newest first, so a busy backend never hides the latest failures
        body = {"size": PULL_PAGE_SIZE, "sort": [{"@timestamp": "desc"}],
                "query": {"range": {"@timestamp": {"gte": f"now-{minutes}m"}}}}
        if search_after is not None:
            body["search_after"] = search_after
        page = _get(url, method="POST", json=body).json().get("hits", {}).get("hits", [])
        hits += page
        if len(page) < PULL_PAGE_SIZE:
            break
        search_after = page[-1].get("sort")
    entries = [e for e in (parse_search_hit(h, application) for h in hits) if e]
    return sorted(entries, key=lambda e: e.timestamp)


def fetch_trace(trace_id: str) -> dict | None:
    """Summary of one trace from the trace backend (Jaeger query API), or None when unavailable."""
    if not settings.otel_enabled or not settings.otel_traces_url:
        return None
    try:
        data = _get(f"{settings.otel_traces_url.rstrip('/')}/api/traces/{trace_id}").json().get("data") or []
    except (TelemetryUnavailable, ValueError):
        return None
    if not data:
        return None
    trace = data[0]
    processes = trace.get("processes", {})
    spans = []
    for span in trace.get("spans", []):
        tags = {t.get("key"): t.get("value") for t in span.get("tags", [])}
        spans.append({
            "service": processes.get(span.get("processID"), {}).get("serviceName"),
            "operation": span.get("operationName"), "duration_ms": round(span.get("duration", 0) / 1000, 1),
            "error": tags.get("error") is True or tags.get("otel.status_code") == "ERROR",
        })
    return {"trace_id": trace_id, "span_count": len(spans), "services": sorted({s["service"] for s in spans if s["service"]}),
            "error_services": sorted({s["service"] for s in spans if s["error"] and s["service"]}),
            "error_spans": [s for s in spans if s["error"]][:10]}


def _prometheus(query: str) -> float | None:
    if not settings.otel_metrics_url:
        return None
    try:
        result = _get(f"{settings.otel_metrics_url.rstrip('/')}/api/v1/query", params={"query": query}).json()
        vector = result["data"]["result"]
        return float(vector[0]["value"][1]) if vector else 0.0
    except (TelemetryUnavailable, KeyError, ValueError, IndexError):
        return None


def service_p95_ms(service: str, window_seconds: int = 120) -> float | None:
    """p95 latency (ms) of a service's server spans over the window, or None when unavailable / no traffic."""
    if not settings.otel_enabled or not settings.otel_metrics_url:
        return None
    window = f"{max(30, int(window_seconds))}s"
    value = _prometheus("histogram_quantile(0.95, sum by (le)(rate(traces_span_metrics_duration_milliseconds_bucket"
                        f'{{service_name="{service}",span_kind="SPAN_KIND_SERVER"}}[{window}])))')
    return value if value is not None and value == value else None


def service_metrics(service: str, window_seconds: int = 120) -> dict | None:
    """Request / error rate of a service over the last `window_seconds` from span metrics (Prometheus), or None
    when unavailable. Verification passes the time since the action so earlier traffic is not counted."""
    if not settings.otel_enabled or not settings.otel_metrics_url:
        return None
    window = f"{max(30, int(window_seconds))}s"
    selector = f'service_name="{service}"'
    total = _prometheus(f"sum(increase(traces_span_metrics_calls_total{{{selector}}}[{window}]))")
    errors = _prometheus(f'sum(increase(traces_span_metrics_calls_total{{{selector},status_code="STATUS_CODE_ERROR"}}[{window}]))')
    if total is None or errors is None:
        return None
    return {"window_seconds": int(window[:-1]), "requests": round(total, 1), "errors": round(errors, 1),
            "error_ratio": round(errors / total, 4) if total else 0.0}


HEALTH_TIMEOUT_SECONDS = 3
_HEALTH_CHECKS = {
    "logs": ("OpenTelemetry Logs (OpenSearch)", lambda: settings.otel_logs_url, "/_cluster/health"),
    "metrics": ("Prometheus Metrics", lambda: settings.otel_metrics_url, "/-/healthy"),
    "traces": ("Jaeger Traces", lambda: settings.otel_traces_url, "/"),
}


def health() -> dict:
    """Reachability of each telemetry backend. An unreachable backend is reported as such; nothing replaces it."""
    result = {"enabled": settings.otel_enabled}
    for signal, (label, base, path) in _HEALTH_CHECKS.items():
        url = base()
        entry = {"source": label, "configured": bool(url), "reachable": None, "detail": None}
        if not settings.otel_enabled:
            entry["detail"] = "OpenTelemetry integration disabled (OTEL_ENABLED=false)"
        elif not url:
            entry["detail"] = "endpoint not configured"
        else:
            try:
                httpx.get(url.rstrip("/") + path, timeout=HEALTH_TIMEOUT_SECONDS).raise_for_status()
                entry["reachable"] = True
            except httpx.HTTPError as exc:
                entry["reachable"] = False
                entry["detail"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        result[signal] = entry
    return result


def service_health(window: str = "5m") -> list[dict]:
    """Per-service request rate and error ratio from span metrics (Prometheus); [] when unavailable."""
    if not settings.otel_enabled or not settings.otel_metrics_url:
        return []
    try:
        base = settings.otel_metrics_url.rstrip("/") + "/api/v1/query"

        def by_service(extra: str = "") -> dict[str, float]:
            query = f"sum by (service_name)(increase(traces_span_metrics_calls_total{{{extra}}}[{window}]))"
            rows = _get(base, params={"query": query}).json()["data"]["result"]
            return {r["metric"].get("service_name", "?"): float(r["value"][1]) for r in rows}

        total, errors = by_service(), by_service('status_code="STATUS_CODE_ERROR"')
    except (TelemetryUnavailable, KeyError, ValueError):
        return []
    return sorted(({"service": s, "requests": round(t, 1), "errors": round(errors.get(s, 0.0), 1),
                    "error_ratio": round(errors.get(s, 0.0) / t, 4) if t else 0.0} for s, t in total.items()),
                  key=lambda r: (-r["error_ratio"], r["service"]))
