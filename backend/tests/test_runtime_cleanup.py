"""The monitored application is the one that was scanned; the old sample application is not a runtime source and
nothing falls back to it. Plus the Case / Communication agents, telemetry health and the dashboard metrics."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import applications as applications_api
from app.config import priority_rules
from app.config.settings import Settings
from app.main import app
from app.services import remediation, resolution_service, verification
from app.services.case_agent import build_case
from app.services.communication_agent import build_message
from app.services.dashboard_service import summarize
from app.services.ingestion import ingest
from app.telemetry import otel
from tests.test_failure_scenarios import flags, setup
from tests.test_resolution import act, events_after, investigation, no_pull, ready  # noqa: F401
from tests.test_telemetry_flow import failing_checkout, incident_for

ROOT = Path(__file__).resolve().parents[2]


# --- no sample application at runtime ---
def test_runtime_source_has_no_sample_application_references():
    pattern = re.compile(r"sample_application|sample app|sample_app", re.IGNORECASE)
    offenders = []
    for base in (ROOT / "backend" / "app", ROOT / "frontend" / "src"):
        for path in base.rglob("*"):
            if path.suffix in (".py", ".ts", ".tsx", ".css", ".html") and "__tests__" not in path.parts and "__pycache__" not in path.parts:
                if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                    offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_sample_log_is_a_test_fixture_not_runtime_data():
    assert not list((ROOT / "data").rglob("*.log"))
    assert (ROOT / "backend" / "tests" / "fixtures" / "sample_application.log").exists()


def test_file_log_import_is_off_by_default(store):
    assert Settings().file_log_ingest is False
    (store.data_dir / "logs" / "extra.log").write_text(
        "2026-01-01T12:00:00Z level=ERROR service=old-service message=boom\n" * 10, encoding="utf-8")
    summary = ingest(store)  # the fixture log and this file sit in data/logs, but runtime never reads them
    assert store.incidents == {} and summary.sources == [] and summary.total_lines == 0


def test_log_ingest_endpoint_is_gone_and_pull_does_not_fall_back(store):
    client = TestClient(app)
    assert client.post("/api/logs/ingest").status_code == 410
    pull = client.post("/api/telemetry/pull")  # OpenTelemetry disabled / unreachable
    assert pull.status_code == 503
    assert store.incidents == {} and store.logs == []
    assert client.get("/api/incidents").json()["total"] == 0
    summary = client.get("/api/dashboard/summary").json()
    assert summary["incidents"]["total"] == 0 and summary["application"]["scanned"] is False


def test_applications_api_reports_only_the_scanned_application(store, tmp_path):
    client = TestClient(app)
    empty = client.get("/api/applications").json()
    assert empty["scanned"] is False and empty["knowledge"] is None and empty["observability"] == "OpenTelemetry"
    setup(store, tmp_path, flags("off"))
    view = client.get("/api/applications").json()
    assert view["knowledge"]["application_name"] == "Shop"
    assert "sample" not in str(view["knowledge"]["services"]).lower()


def test_startup_scan_uses_configuration_and_reports_unavailability(store, tmp_path, monkeypatch):
    from tests.mini_shop import make_app

    shop = make_app(tmp_path)
    monkeypatch.setattr(applications_api, "settings", Settings(application_name="Configured Shop", application_path=str(shop)))
    applications_api.scan_configured_application()
    assert store.application.application_name == "Configured Shop" and applications_api.scan_error is None
    store.application = None
    monkeypatch.setattr(applications_api, "settings", Settings(application_path=str(tmp_path / "missing")))
    applications_api.scan_configured_application()
    assert store.application is None  # nothing is substituted
    assert "Application unavailable" in applications_api.scan_error
    view = applications_api.application_view(None)
    assert view["scanned"] is False and view["scan_error"]
    applications_api.scan_error = None


# --- telemetry health ---
def test_telemetry_health_reports_unavailable_backends(monkeypatch):
    disabled = otel.health()
    assert disabled["logs"]["reachable"] is None and "disabled" in disabled["logs"]["detail"]
    monkeypatch.setattr(otel, "settings", Settings(otel_enabled=True, otel_logs_url="http://127.0.0.1:9", otel_metrics_url=None))
    health = otel.health()
    assert health["logs"]["reachable"] is False and health["logs"]["source"].startswith("OpenTelemetry Logs")
    assert health["metrics"]["configured"] is False and health["traces"]["configured"] is False
    assert otel.service_health() == []


# --- configurable priority policy ---
def test_priority_thresholds_are_configurable(monkeypatch):
    monkeypatch.setenv("PRIORITY_P1_MIN", "90")
    monkeypatch.setenv("PRIORITY_P2_MIN", "oops")
    assert priority_rules._threshold("P1", 80.0) == 90.0
    assert priority_rules._threshold("P2", 65.0) == 65.0  # invalid values keep the default


# --- resolution option detail ---
def test_options_carry_title_prerequisites_and_expected_outcome(ready):  # noqa: F811
    store, incident = ready
    record = resolution_service.refresh(store, incident.incident_id)
    assert len(record.options) >= 3
    for option in record.options:
        assert option.title and option.prerequisites and option.expected_outcome and option.target_service == "payment"


# --- Case and Communication agents ---
def test_case_and_communication_follow_the_records(ready):  # noqa: F811
    store, incident = ready
    assert build_case(store, incident)["final_status"] == "OPEN"  # no resolution proposed yet
    resolution_service.refresh(store, incident.incident_id)
    case = build_case(store, incident)
    assert case["final_status"] == "AWAITING_DECISION" and case["application"] == "Shop" and case["observability_source"] == "OpenTelemetry"
    assert [e["stage"] for e in case["timeline"]][:2] == ["Detection", "Investigation"]
    attempt = act(store, incident)
    otel.save_events(store.data_dir, events_after(attempt, "payment", "INFO", 6))
    verification.verify(store, incident.incident_id, now=attempt.executed_at + timedelta(seconds=120), pull=no_pull)
    case = build_case(store, incident)
    assert case["final_status"] == "RESOLVED" and case["approvals"][0]["approved_by"] == "alice"
    message = build_message(store, incident)
    assert "Resolved: recovery was verified" in message["body"] and "no AI" in message["generated_by"]
    assert case["timeline"][-1]["stage"] == "Verification"


def test_communication_does_not_claim_certainty_it_does_not_have(store, tmp_path):
    _, incident = setup(store, tmp_path, flags("on"))
    message = build_message(store, incident)
    assert "UNKNOWN" in message["body"] and "not completed" in message["body"]  # no investigation yet
    store.save_investigation(investigation(incident.incident_id, confidence=0.3))
    assert "possible cause (low confidence" in build_message(store, incident)["body"]
    client = TestClient(app)
    assert client.get(f"/api/incidents/{incident.incident_id}/case").json()["final_status"] == "OPEN"
    assert client.get(f"/api/incidents/{incident.incident_id}/communication").json()["status"] == "OPEN"
    assert client.get("/api/incidents/INC-NOPE0000/case").status_code == 404


# --- verification evidence and dashboard metrics ---
def test_verification_reports_a_scenario_that_is_still_enabled(store, tmp_path, monkeypatch):
    _, incident = setup(store, tmp_path, flags("on"))
    store.save_investigation(investigation(incident.incident_id))
    monkeypatch.setattr(verification, "settings", Settings(verification_wait_seconds=0))
    record = resolution_service.refresh(store, incident.incident_id)
    attempt = remediation.execute(store, incident.incident_id, record.recommended_option_id, "alice")
    otel.save_events(store.data_dir, events_after(attempt, "payment", "ERROR", 12))
    result = verification.verify(store, incident.incident_id, now=attempt.executed_at + timedelta(seconds=120), pull=no_pull)
    assert result.status == "FAILED" and result.after["scenario_state"] == {"paymentFailure": True}
    assert "paymentFailure" in result.reason  # context only: the decision came from telemetry


def test_dashboard_metrics_come_from_the_records(ready):  # noqa: F811
    store, incident = ready
    attempt = act(store, incident)
    otel.save_events(store.data_dir, events_after(attempt, "payment", "INFO", 6))
    verification.verify(store, incident.incident_id, now=attempt.executed_at + timedelta(seconds=120), pull=no_pull)
    analytics = summarize(store)["analytics"]
    assert analytics["human_approval_count"]["value"] == 1 and analytics["human_takeover_count"]["value"] == 0
    assert analytics["verification_outcomes"]["value"] == {"SUCCESS": 1}
    assert analytics["resolution_rate"]["value"] > 0 and analytics["mttr"]["available"] is True


def test_state_write_retries_a_transiently_locked_file(tmp_path, monkeypatch):
    import os

    from app.state import store as store_module

    real_replace, calls = os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked by a sync client")
        return real_replace(src, dst)

    monkeypatch.setattr(store_module.os, "replace", flaky)
    monkeypatch.setattr(store_module.time, "sleep", lambda s: None)
    target = tmp_path / "state.json"
    store_module._write_json(target, {"ok": True})
    assert calls["n"] == 3 and target.read_text(encoding="utf-8").strip().startswith("{")
    monkeypatch.setattr(store_module.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError("always")))
    with pytest.raises(PermissionError):
        store_module._write_json(target, {"ok": False})  # a persistent failure is still reported


def test_telemetry_from_services_outside_the_application_is_ignored(store, tmp_path):
    from tests.mini_shop import make_app
    from app.application.discovery import discover_application
    from tests.test_telemetry_flow import payload, record

    store.save_application(discover_application("Shop", str(make_app(tmp_path))))
    noise = otel.parse_otlp_logs(payload([record("otelcol-contrib", i, "ERROR", "scrape failed", f"n{i}") for i in range(8)]), "Shop")
    otel.save_events(store.data_dir, failing_checkout() + noise)
    summary = ingest(store)
    assert summary.ignored_services == ["otelcol-contrib"]
    assert {i.service for i in store.incidents.values()} == {"payment", "checkout"}  # the Collector's own logs are not Shop incidents
