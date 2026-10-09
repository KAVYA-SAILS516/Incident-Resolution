"""Metric-based detection: failures that are visible only in Prometheus span metrics still open incidents."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import telemetry as telemetry_api
from app.application.discovery import discover_application
from app.config.settings import Settings
from app.main import app
from app.services import remediation, resolution_service, verification
from app.services.ingestion import ingest
from app.telemetry import metric_detection, otel
from tests.mini_shop import make_app
from tests.test_resolution import investigation
from tests.test_telemetry_flow import incident_for

NOW = 1_790_000_010  # an arbitrary instant; detection aligns to the 30 s step
STEP = 30


def settings_on(**kw):
    return Settings(otel_enabled=True, otel_metrics_url="http://prom", **kw)


def fake_prometheus(*, requests=10.0, errors=6.0, p95=200.0, base=200.0, services=("payment",), steps=8):
    """A range-query stand-in answering the four queries detection makes, for every step of the period."""
    def query(q, start, end, step):
        stamps = range(start, end + 1, step)
        value = (base if "[30m]" in q else p95) if "histogram_quantile" in q else (errors if "STATUS_CODE_ERROR" in q else requests)
        return {s: {t: float(value) for t in list(stamps)[-steps:]} for s in services}
    return query


def detect(monkeypatch, **kw):
    monkeypatch.setattr(metric_detection, "settings", settings_on())
    return metric_detection.detect_metric_events("Shop", 10, query=fake_prometheus(**kw), now=NOW)


# --- detection rules ---
def test_high_error_ratio_with_enough_traffic_becomes_events(monkeypatch):
    events = detect(monkeypatch, errors=6, requests=10)
    assert len(events) == 8 and {e.error_type for e in events} == {"METRIC_ERROR_RATIO_HIGH"}
    e = events[0]
    assert (e.service, e.level, e.source, e.application) == ("payment", "ERROR", "prometheus", "Shop")
    assert e.attributes["metric.value"] == 0.6 and e.attributes["source.label"] == "Prometheus Metrics" and e.is_abnormal
    assert "60%" in e.message and "6 of 10" in e.message


def test_low_ratio_or_too_little_traffic_is_not_an_anomaly(monkeypatch):
    assert detect(monkeypatch, errors=1, requests=10) == []  # 10% < threshold
    assert detect(monkeypatch, errors=2, requests=2) == []  # 100% but only 2 spans: not enough traffic


def test_latency_jump_over_the_baseline_is_detected_and_a_steady_slow_service_is_not(monkeypatch):
    jump = detect(monkeypatch, errors=0, p95=5000, base=300)
    assert {e.error_type for e in jump} == {"METRIC_LATENCY_HIGH"} and "16.7x" in jump[0].message
    assert detect(monkeypatch, errors=0, p95=5000, base=5000) == []  # always slow: not an anomaly
    assert detect(monkeypatch, errors=0, p95=400, base=50) == []  # big factor but under the absolute floor


def test_events_are_deterministic_so_repeated_reads_deduplicate(monkeypatch, tmp_path):
    first, second = detect(monkeypatch), detect(monkeypatch)
    assert [e.log_id for e in first] == [e.log_id for e in second]
    otel.save_events(tmp_path, first)
    assert len(otel.save_events(tmp_path, second)) == len(first)
    assert all(e.timestamp.timestamp() % STEP == 0 for e in first)


def test_disabled_or_unreachable_prometheus_is_reported_not_faked(monkeypatch):
    with pytest.raises(otel.TelemetryUnavailable, match="disabled"):
        metric_detection.detect_metric_events("Shop", 10, query=fake_prometheus())
    monkeypatch.setattr(metric_detection, "settings", Settings(otel_enabled=True, otel_metrics_url=None))
    with pytest.raises(otel.TelemetryUnavailable, match="OTEL_METRICS_URL"):
        metric_detection.detect_metric_events("Shop", 10)
    monkeypatch.setattr(metric_detection, "settings", settings_on(metric_detection=False))
    assert metric_detection.detect_metric_events("Shop", 10, query=fake_prometheus()) == []


# --- through the existing pipeline ---
@pytest.fixture
def scanned(store, tmp_path):
    store.save_application(discover_application("Shop", str(make_app(tmp_path))))
    return store


def test_metric_anomalies_open_an_incident_with_application_context(scanned, monkeypatch):
    otel.save_events(scanned.data_dir, detect(monkeypatch, services=("payment",)))
    ingest(scanned)
    incident = incident_for(scanned, "payment")
    assert incident.error_type == "METRIC_ERROR_RATIO_HIGH" and incident.occurrences == 8
    assert incident.application == "Shop" and incident.service_criticality == "CRITICAL"
    assert incident.priority in ("P1", "P2") and "critical service" in incident.priority_reason
    assert {scanned.logs_by_id[i].source for i in incident.log_ids} == {"prometheus"}


def test_failing_dependency_and_caller_correlate_by_time_and_dependency(scanned, monkeypatch):
    otel.save_events(scanned.data_dir, detect(monkeypatch, services=("payment", "checkout")))
    ingest(scanned)
    payment, checkout = incident_for(scanned, "payment"), incident_for(scanned, "checkout")
    link = payment.correlated_incidents[0]
    assert (link["incident_id"], link["relation"], link["basis"], link["shared_traces"]) == (checkout.incident_id, "dependent", "time+dependency", 0)
    assert checkout.correlated_incidents[0]["relation"] == "dependency"
    assert set(payment.affected_services) == {"payment", "checkout"}


def test_unrelated_services_failing_together_are_not_correlated(scanned, monkeypatch):
    otel.save_events(scanned.data_dir, detect(monkeypatch, services=("payment", "email")))  # no dependency between them
    ingest(scanned)
    assert incident_for(scanned, "payment").correlated_incidents == []


# --- API ---
def test_pull_adds_metric_anomalies_and_survives_prometheus_failure(scanned, monkeypatch):
    monkeypatch.setattr(telemetry_api.otel, "fetch_logs", lambda application, minutes=None: [])
    monkeypatch.setattr(telemetry_api, "detect_metric_events", lambda application: detect(monkeypatch))
    client = TestClient(app)
    body = client.post("/api/telemetry/pull").json()
    assert body["metric_anomaly_events"] == 8 and body["incidents_detected"] == 1 and body["metrics_error"] is None

    def down(application):
        raise otel.TelemetryUnavailable("prometheus down")

    monkeypatch.setattr(telemetry_api, "detect_metric_events", down)
    body = client.post("/api/telemetry/pull").json()
    assert body["metrics_error"] == "prometheus down" and body["metric_anomaly_events"] == 0  # reported, logs path unaffected


# --- verification of a metric-detected incident ---
@pytest.fixture
def metric_incident(scanned, monkeypatch):
    otel.save_events(scanned.data_dir, detect(monkeypatch, services=("payment",)))
    ingest(scanned)
    incident = incident_for(scanned, "payment")
    scanned.save_investigation(investigation(incident.incident_id))
    monkeypatch.setattr(verification, "settings", Settings(verification_wait_seconds=0, otel_enabled=True))
    record = resolution_service.refresh(scanned, incident.incident_id)
    attempt = remediation.execute(scanned, incident.incident_id, record.recommended_option_id, "alice")
    later = attempt.executed_at + timedelta(seconds=120)
    # fresh telemetry exists after the action (any service), so the freshness check passes
    fresh = otel.parse_otlp_logs({}, "Shop")
    from tests.test_telemetry_flow import payload, record as otlp_record
    entries = otel.parse_otlp_logs(payload([otlp_record("email", 0, "INFO", "ok", "t")]), "Shop")
    otel.save_events(scanned.data_dir, [e.model_copy(update={"timestamp": attempt.executed_at + timedelta(seconds=5)}) for e in entries])
    return scanned, incident, later


def run_verify(store, incident, later, **kw):
    return verification.verify(store, incident.incident_id, now=later, pull=lambda a, m: [], **kw)


def test_metric_incident_recovers_when_the_error_ratio_falls(metric_incident):
    store, incident, later = metric_incident
    result = run_verify(store, incident, later, metrics=lambda s, w: {"requests": 40.0, "errors": 0.0, "error_ratio": 0.0})
    assert result.status == "SUCCESS" and "peaked at 60%" in result.reason
    assert store.resolutions[incident.incident_id].state == "resolved"


def test_metric_incident_fails_when_errors_continue_and_unknown_without_traffic_or_metrics(metric_incident):
    store, incident, later = metric_incident
    quiet = run_verify(store, incident, later, metrics=lambda s, w: {"requests": 1.0, "errors": 0.0, "error_ratio": 0.0})
    assert quiet.status == "UNKNOWN" and "too little traffic" in quiet.reason
    none = run_verify(store, incident, later, metrics=lambda s, w: None)
    assert none.status == "UNKNOWN" and "could not be read" in none.reason
    assert store.resolutions[incident.incident_id].state == "executed"  # still waiting, not resolved by silence
    bad = run_verify(store, incident, later, metrics=lambda s, w: {"requests": 40.0, "errors": 30.0, "error_ratio": 0.75})
    assert bad.status == "FAILED" and store.resolutions[incident.incident_id].failed_options


# --- automatic reading (no button click needed) ---
def test_background_poll_creates_incidents_without_a_click(scanned, monkeypatch):
    import asyncio

    monkeypatch.setattr(telemetry_api.otel, "fetch_logs", lambda application, minutes=None: [])
    monkeypatch.setattr(telemetry_api, "detect_metric_events", lambda application: detect(monkeypatch))
    assert asyncio.run(telemetry_api.auto_pull_once())["incidents_detected"] == 1
    assert incident_for(scanned, "payment").error_type == "METRIC_ERROR_RATIO_HIGH"


def test_background_poll_waits_for_an_application_and_survives_failures(store, monkeypatch):
    import asyncio

    assert asyncio.run(telemetry_api.auto_pull_once()) is None  # nothing scanned yet: nothing is read
    store.application = type("A", (), {"application_name": "Shop"})()

    def down(application, minutes=None):
        raise otel.TelemetryUnavailable("opensearch down")

    monkeypatch.setattr(telemetry_api.otel, "fetch_logs", down)
    assert asyncio.run(telemetry_api.auto_pull_once()) is None  # logged, never raised: the loop keeps running

    def boom(application, minutes=None):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(telemetry_api.otel, "fetch_logs", boom)
    assert asyncio.run(telemetry_api.auto_pull_once()) is None


def test_poll_interval_is_configurable(monkeypatch):
    assert Settings().telemetry_poll_seconds == 60
    monkeypatch.setenv("TELEMETRY_POLL_SECONDS", "0")
    assert Settings().telemetry_poll_seconds == 0  # 0 turns the background read off
