"""Resolution Decision -> guarded remediation -> verification (with retry and human takeover)."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.application.discovery import discover_application
from app.config.settings import Settings
from app.main import app
from app.schemas.investigation import InvestigationRecord
from app.services import remediation, resolution_service, verification
from app.services.ingestion import ingest
from app.services.priority_engine import PriorityInputs, score_priority
from app.state.store import JsonStore
from app.telemetry import otel
from tests.mini_shop import make_app
from tests.test_telemetry_flow import T0, failing_checkout, incident_for, nano, payload, record


def investigation(incident_id, confidence=0.8):
    return InvestigationRecord(incident_id=incident_id, root_cause="Payment failing on invalid token", confidence=confidence,
                               evidence=[], analysis="", unknowns=[], insufficient_evidence=False, model="fake",
                               based_on_occurrences=8, duration_ms=1, created_at=datetime.now(timezone.utc))


@pytest.fixture
def ready(store, tmp_path, monkeypatch):
    store.save_application(discover_application("Shop", str(make_app(tmp_path))))
    otel.save_events(store.data_dir, failing_checkout())
    ingest(store)
    incident = incident_for(store, "payment")
    store.save_investigation(investigation(incident.incident_id))
    monkeypatch.setattr(verification, "settings", Settings(verification_wait_seconds=0))
    return store, incident


# --- priority policy ---
BASE = PriorityInputs(workflow="Payment", criticality="CRITICAL", severity="ERROR", status_code=503,
                      occurrences=60, host_count=4, peak_per_minute=10, service_criticality="CRITICAL")


def test_p1_to_p4_bands_work_with_service_criticality():
    scores = {
        "P1": score_priority(BASE),
        "P2": score_priority(replace(BASE, occurrences=10, host_count=1, peak_per_minute=1, status_code=429, severity="WARNING")),
        "P3": score_priority(replace(BASE, criticality="MEDIUM", service_criticality="MEDIUM", occurrences=10, host_count=1,
                                     peak_per_minute=1, status_code=429, severity="WARNING")),
        "P4": score_priority(replace(BASE, criticality="LOW", service_criticality="LOW", occurrences=5, host_count=1,
                                     peak_per_minute=1, status_code=401, severity="INFO")),
    }
    assert {k: v.priority for k, v in scores.items()} == {k: k for k in scores}
    assert scores["P1"].score > scores["P2"].score > scores["P3"].score > scores["P4"].score
    assert sum(f.weight for f in scores["P1"].factors) == pytest.approx(1.0)


def test_service_criticality_raises_priority_but_is_not_the_priority():
    low = score_priority(replace(BASE, service_criticality="LOW"))
    high = score_priority(BASE)
    unknown = score_priority(replace(BASE, service_criticality=None))
    assert high.score > low.score
    assert unknown.score > low.score  # original model unchanged when the service is not known
    assert "service_criticality" not in {f.name for f in unknown.factors}


# --- decision ---
def test_options_are_registered_actions_with_full_attributes(ready):
    store, incident = ready
    record_ = resolution_service.refresh(store, incident.incident_id)
    assert {o.action for o in record_.options} <= {"restart_service", "rollback_configuration", "scale_service", "clear_cache"}
    best = record_.options[0]
    assert (best.action, best.target_service) == ("rollback_configuration", "payment")  # documented failure scenario
    for option in record_.options:
        assert option.risk and option.impact and option.blast_radius and option.reversibility and 0 <= option.confidence <= 1
        assert option.execution_mode in ("AUTO_EXECUTE", "HUMAN_APPROVAL", "HUMAN_TAKEOVER")
    assert record_.recommended_option_id == best.option_id and record_.decision == "HUMAN_APPROVAL"
    assert "checkout" in best.blast_radius  # dependents come from the application knowledge


def test_dependency_failing_in_same_traces_is_a_target(ready):
    store, _ = ready
    checkout = incident_for(store, "checkout")
    record_ = resolution_service.refresh(store, checkout.incident_id)
    assert {o.target_service for o in record_.options} == {"checkout", "payment"}


def test_without_investigation_or_application_a_person_takes_over(store):
    otel.save_events(store.data_dir, failing_checkout())
    ingest(store)
    incident = incident_for(store, "payment")
    record_ = resolution_service.refresh(store, incident.incident_id)
    assert record_.options == [] and record_.decision == "HUMAN_TAKEOVER"


def test_execution_mode_policy(ready):
    _, incident = ready
    low_risk = incident.model_copy(update={"priority": "P4", "service_criticality": "LOW"})
    assert resolution_service.execution_mode(low_risk, "LOW", "REVERSIBLE", 0.9) == "AUTO_EXECUTE"
    assert resolution_service.execution_mode(low_risk, "LOW", "REVERSIBLE", 0.5) == "HUMAN_APPROVAL"
    assert resolution_service.execution_mode(low_risk, "MEDIUM", "REVERSIBLE", 0.9) == "HUMAN_APPROVAL"
    assert resolution_service.execution_mode(low_risk, "HIGH", "REVERSIBLE", 0.9) == "HUMAN_TAKEOVER"
    assert resolution_service.execution_mode(low_risk, "LOW", "REVERSIBLE", 0.2) == "HUMAN_TAKEOVER"
    critical = incident.model_copy(update={"priority": "P4", "service_criticality": "CRITICAL"})
    assert resolution_service.execution_mode(critical, "LOW", "REVERSIBLE", 0.95) == "HUMAN_APPROVAL"
    assert resolution_service.execution_mode(incident.model_copy(update={"priority": "P1"}), "LOW", "REVERSIBLE", 0.95) == "HUMAN_APPROVAL"


# --- remediation safety ---
def test_remediation_is_guarded(ready, store):
    store, incident = ready
    record_ = resolution_service.refresh(store, incident.incident_id)
    with pytest.raises(remediation.RemediationRejected, match="not an option"):
        remediation.execute(store, incident.incident_id, "rm -rf /", "alice")
    with pytest.raises(remediation.RemediationRejected, match="not an option"):
        remediation.execute(store, incident.incident_id, "restart_service:not-in-app", "alice")
    with pytest.raises(remediation.RemediationRejected, match="requires human approval"):
        remediation.execute(store, incident.incident_id, record_.recommended_option_id, "")
    takeover = next(o for o in record_.options if o.execution_mode == "HUMAN_TAKEOVER")
    with pytest.raises(remediation.RemediationRejected, match="by a person"):
        remediation.execute(store, incident.incident_id, takeover.option_id, "alice")
    attempt = remediation.execute(store, incident.incident_id, record_.recommended_option_id, "alice")
    assert attempt.mode == "simulated" and "No infrastructure was changed" in attempt.detail and attempt.approved_by == "alice"
    with pytest.raises(remediation.RemediationRejected, match="not been verified"):
        remediation.execute(store, incident.incident_id, record_.recommended_option_id, "alice")


def test_unknown_service_cannot_be_remediated(ready):
    store, incident = ready
    record_ = resolution_service.refresh(store, incident.incident_id)
    store.application = None
    with pytest.raises(remediation.RemediationRejected, match="not a known service"):
        remediation.execute(store, incident.incident_id, record_.recommended_option_id, "alice")


# --- verification ---
def act(store, incident):
    record_ = resolution_service.refresh(store, incident.incident_id)
    return remediation.execute(store, incident.incident_id, record_.recommended_option_id, "alice")


def events_after(attempt, service, level, count):
    recs = [record(service, 0, level, "Payment request failed. Invalid token", f"v{i}") for i in range(count)]
    entries = otel.parse_otlp_logs(payload(recs), "Shop")
    return [e.model_copy(update={"timestamp": attempt.executed_at + timedelta(seconds=10 + i * 5),
                                 "log_id": f"opentelemetry:v-{service}-{level}-{i}"}) for i, e in enumerate(entries)]


def no_pull(application, minutes):
    return []


def test_verification_nothing_to_verify(ready):
    store, incident = ready
    with pytest.raises(verification.VerificationNotReady):
        verification.verify(store, incident.incident_id)


def test_verification_too_early_and_without_telemetry_is_inconclusive(ready, monkeypatch):
    store, incident = ready
    attempt = act(store, incident)
    monkeypatch.setattr(verification, "settings", Settings(verification_wait_seconds=60))
    early = verification.verify(store, incident.incident_id, now=attempt.executed_at + timedelta(seconds=5), pull=no_pull)
    assert early.status == "UNKNOWN" and "Too early" in early.reason
    monkeypatch.setattr(verification, "settings", Settings(verification_wait_seconds=0))
    silent = verification.verify(store, incident.incident_id, now=attempt.executed_at + timedelta(seconds=120), pull=no_pull)
    assert silent.status == "UNKNOWN" and "No telemetry arrived" in silent.reason  # silence is not recovery
    assert store.resolutions[incident.incident_id].state == "executed"  # still waiting for a real answer


def test_verification_needs_enough_traffic(ready):
    store, incident = ready
    attempt = act(store, incident)
    otel.save_events(store.data_dir, events_after(attempt, "payment", "INFO", 1))  # one quiet event is not recovery
    result = verification.verify(store, incident.incident_id, now=attempt.executed_at + timedelta(seconds=120), pull=no_pull)
    assert result.status == "UNKNOWN" and "too little traffic" in result.reason
    assert store.resolutions[incident.incident_id].state == "executed"


def test_verification_success_from_healthy_telemetry(ready):
    store, incident = ready
    attempt = act(store, incident)
    otel.save_events(store.data_dir, events_after(attempt, "payment", "INFO", 6))
    result = verification.verify(store, incident.incident_id, now=attempt.executed_at + timedelta(seconds=120), pull=no_pull)
    assert result.status == "SUCCESS" and result.after["abnormal_events"] == 0 and result.before["abnormal_ratio"] == 1.0
    record_ = store.resolutions[incident.incident_id]
    assert record_.state == "resolved" and not record_.failed_options


def test_failed_verification_excludes_option_then_hands_over_to_a_person(ready):
    store, incident = ready
    first = act(store, incident)
    first_option = first.option_id
    otel.save_events(store.data_dir, events_after(first, "payment", "ERROR", 12))
    failed = verification.verify(store, incident.incident_id, now=first.executed_at + timedelta(seconds=120), pull=no_pull)
    assert failed.status == "FAILED"
    record_ = store.resolutions[incident.incident_id]
    assert record_.failed_options == [first_option] and record_.state == "proposed"
    assert first_option not in {o.option_id for o in record_.options}  # a failed option is never offered again
    assert record_.decision != "HUMAN_TAKEOVER" and record_.recommended_option_id != first_option

    second = remediation.execute(store, incident.incident_id, record_.recommended_option_id, "alice")
    otel.save_events(store.data_dir, events_after(second, "payment", "ERROR", 12))
    verification.verify(store, incident.incident_id, now=second.executed_at + timedelta(seconds=120), pull=no_pull)
    final = store.resolutions[incident.incident_id]
    assert final.decision == "HUMAN_TAKEOVER" and final.state == "human_takeover"  # maximum attempts reached
    assert final.recommended_option_id is None


# --- API ---
def test_resolution_api_flow(ready):
    store, incident = ready
    client = TestClient(app)
    detail = client.get(f"/api/incidents/{incident.incident_id}").json()
    assert detail["resolution"]["decision"] == "HUMAN_APPROVAL" and detail["evidence"]["application_context"]["criticality"] == "CRITICAL"
    option = detail["resolution"]["recommended_option_id"]
    assert client.post(f"/api/incidents/{incident.incident_id}/resolution/execute",
                       json={"option_id": "bogus"}).status_code == 409
    done = client.post(f"/api/incidents/{incident.incident_id}/resolution/execute", json={"option_id": option, "approved_by": "alice"})
    assert done.status_code == 200 and done.json()["state"] == "executed"
    assert client.post(f"/api/incidents/{incident.incident_id}/resolution/verify").json()["status"] == "UNKNOWN"
    taken = client.post(f"/api/incidents/{incident.incident_id}/resolution/takeover").json()
    assert taken["decision"] == "HUMAN_TAKEOVER" and taken["state"] == "human_takeover"
    activity = client.get("/api/activity").json()
    assert {"Resolution decision", "Remediation"} <= {e["stage"] for e in activity["events"]}
    assert client.get("/api/incidents/INC-NOPE0000/resolution").status_code == 404
