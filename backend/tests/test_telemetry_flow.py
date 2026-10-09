"""OpenTelemetry events -> existing detection -> correlation -> triage -> investigation (with application knowledge)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.investigation_agent import investigate
from app.application.discovery import discover_application
from app.config.settings import Settings
from app.schemas.log import LogEntry
from app.services.evidence_service import build_evidence
from app.services.ingestion import ingest
from app.telemetry import otel
from tests.mini_shop import make_app

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def nano(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1e9))


def record(service, offset_s, severity, body, trace, span="s1", attrs=None):
    return {
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}},
                                    {"key": "service.namespace", "value": {"stringValue": "opentelemetry-demo"}}]},
        "record": {"timeUnixNano": nano(T0 + timedelta(seconds=offset_s)), "severityText": severity,
                   "body": {"stringValue": body}, "traceId": trace, "spanId": span,
                   "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in (attrs or {}).items()]},
    }


def payload(records):
    return {"resourceLogs": [{"resource": r["resource"], "scopeLogs": [{"logRecords": [r["record"]]}]} for r in records]}


def failing_checkout(n_payment=8, n_checkout=6):
    recs = []
    for i in range(n_payment):
        recs.append(record("payment", i * 10, "ERROR", "Payment request failed. Invalid token", f"trace{i:02d}"))
    for i in range(n_checkout):
        recs.append(record("checkout", i * 10 + 1, "ERROR", f"failed to charge card: rpc error code {i}", f"trace{i:02d}"))
    recs.append(record("email", 5, "INFO", "email sent", "trace00"))
    return otel.parse_otlp_logs(payload(recs), "Shop")


@pytest.fixture
def scanned(store, tmp_path):
    store.save_application(discover_application("Shop", str(make_app(tmp_path))))
    return store


def with_telemetry(store, entries):
    otel.save_events(store.data_dir, entries)
    ingest(store)


def incident_for(store, service):
    return next(i for i in store.incidents.values() if i.service == service)


# --- event parsing ---
def test_otlp_logs_become_log_entries():
    entry = failing_checkout()[0]
    assert (entry.service, entry.level, entry.source, entry.application) == ("payment", "ERROR", "opentelemetry", "Shop")
    assert entry.trace_id == "trace00" and entry.span_id == "s1"
    assert entry.error_type == "PAYMENT_REQUEST_FAILED"
    assert entry.is_abnormal and entry.attributes["resource.service.namespace"] == "opentelemetry-demo"


def test_severity_numbers_and_missing_fields():
    rec = payload([{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "cart"}}]},
                    "record": {"timeUnixNano": nano(T0), "severityNumber": 17, "body": {"stringValue": "boom"}}}])
    assert otel.parse_otlp_logs(rec)[0].level == "ERROR"
    no_service = {"resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": [{"logRecords": [
        {"timeUnixNano": nano(T0), "severityText": "ERROR", "body": {"stringValue": "x"}}]}]}]}
    assert otel.parse_otlp_logs(no_service) == []  # an event without a service cannot be attributed
    assert otel.parse_otlp_logs({}) == []


def test_search_hit_formats():
    hit = {"_source": {"@timestamp": "2026-01-01T12:00:00Z", "severityText": "WARN", "body": "slow call",
                       "traceId": "abc", "spanId": "def", "resource": {"service.name": "cart", "host.name": "h1"},
                       "attributes": {"http.response.status_code": 503}}}
    entry = otel.parse_search_hit(hit, "Shop")
    assert (entry.service, entry.level, entry.status_code, entry.host, entry.trace_id) == ("cart", "WARNING", 503, "h1", "abc")
    nested = {"_source": {"time": 1767268800000, "severityText": "ERROR", "body": "{\"msg\": \"bad\", \"level\": \"error\"}",
                          "resource": {"attributes": {"service": {"name": "payment"}}}}}
    assert otel.parse_search_hit(nested).message == "bad"
    assert otel.parse_search_hit({"_source": {"body": "no time or service"}}) is None


def test_events_persist_and_deduplicate(tmp_path):
    entries = failing_checkout()
    otel.save_events(tmp_path, entries)
    stored = otel.save_events(tmp_path, entries)  # same events again
    assert len(stored) == len(entries) == len(otel.load_events(tmp_path))


def test_missing_telemetry_is_reported_not_faked(monkeypatch):
    with pytest.raises(otel.TelemetryUnavailable, match="disabled"):
        otel.fetch_logs("Shop")
    monkeypatch.setattr(otel, "settings", Settings(otel_enabled=True))
    with pytest.raises(otel.TelemetryUnavailable, match="OTEL_LOGS_URL"):
        otel.fetch_logs("Shop")
    assert otel.fetch_trace("abc") is None and otel.service_metrics("payment") is None


# --- detection, correlation, triage ---
def test_telemetry_flows_through_existing_detection(scanned):
    with_telemetry(scanned, failing_checkout())
    payment, checkout = incident_for(scanned, "payment"), incident_for(scanned, "checkout")
    assert payment.occurrences == 8 and checkout.occurrences == 6
    assert "opentelemetry" in scanned.summary.sources
    assert {i.service for i in scanned.incidents.values()} == {"payment", "checkout"}  # no local log file is read
    assert not any(i.service == "email" for i in scanned.incidents.values())  # INFO logs are not incidents


def test_trace_correlation_and_dependency_relation(scanned):
    with_telemetry(scanned, failing_checkout())
    payment, checkout = incident_for(scanned, "payment"), incident_for(scanned, "checkout")
    assert payment.affected_services == ["checkout", "payment"]
    assert payment.correlated_incidents[0]["incident_id"] == checkout.incident_id
    assert payment.correlated_incidents[0]["relation"] == "dependent"  # checkout calls payment
    assert checkout.correlated_incidents[0]["relation"] == "dependency"  # payment is a dependency of checkout
    assert checkout.correlated_incidents[0]["shared_traces"] == 6


def test_triage_uses_application_context(scanned):
    with_telemetry(scanned, failing_checkout())
    payment = incident_for(scanned, "payment")
    assert payment.application == "Shop" and payment.service_criticality == "CRITICAL"
    assert "service_criticality" in {f.name for f in payment.priority_factors}
    assert pytest.approx(sum(f.weight for f in payment.priority_factors)) == 1.0
    assert "critical service of Shop" in payment.priority_reason and "also failed in checkout" in payment.priority_reason
    assert payment.priority in ("P1", "P2")
    with_app_score = payment.priority_score
    scanned.application = None  # same events, no application knowledge
    ingest(scanned)
    assert incident_for(scanned, "payment").service_criticality is None
    assert incident_for(scanned, "payment").priority_score < with_app_score


def test_unknown_service_keeps_original_scoring(scanned):
    entries = otel.parse_otlp_logs(payload([record("reports", i, "ERROR", "report failed", f"r{i}") for i in range(6)]), "Shop")
    with_telemetry(scanned, entries)
    incident = incident_for(scanned, "reports")
    assert incident.service_criticality is None  # declared nowhere -> unknown
    assert "service_criticality" not in {f.name for f in incident.priority_factors}


# --- investigation sees the application ---
def test_investigation_receives_application_knowledge(scanned, fake_llm):
    with_telemetry(scanned, failing_checkout())
    incident = incident_for(scanned, "checkout")
    evidence = build_evidence(incident, scanned)
    assert evidence["application_context"]["criticality"] == "CRITICAL"
    assert evidence["application_context"]["depends_on"] == ["email", "payment"]
    assert evidence["correlation"]["correlated_incidents"][0]["service"] == "payment"
    assert evidence["correlation"]["dependency_contexts"][0]["purpose"].startswith("This service is responsible")
    assert any("Correlated: payment" in fact for fact in evidence["facts"])
    record_ = asyncio.run(investigate(incident, scanned))
    assert {"get_application_context", "get_trace_correlation"} <= set(record_.tools_called)
