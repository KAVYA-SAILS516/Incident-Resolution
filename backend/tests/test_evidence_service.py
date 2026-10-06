import json

from app.agents.investigation_agent import LOG_ONLY_MAX_CONFIDENCE, validate_investigation
from app.agents.recommendation_agent import validate_recommendations
from app.schemas.investigation import INSUFFICIENT_EVIDENCE, InvestigationOutput
from app.schemas.recommendation import RecommendationOutput
from app.services.evidence_service import MAX_REPRESENTATIVE_LOGS, build_evidence, spike_minutes
from app.services.ingestion import ingest
from app.tools.runbook_tools import load_runbook
from tests.conftest import INVESTIGATION_PAYLOAD, RECOMMENDATION_PAYLOAD


def _top_incident(store):
    ingest(store)
    return max(store.incidents.values(), key=lambda i: i.priority_score)


def test_evidence_is_aggregated_and_bounded(store):
    incident = _top_incident(store)
    evidence = build_evidence(incident, store)
    assert set(evidence) >= {"incident", "facts", "statistics", "timeline", "baseline", "related_errors",
                             "representative_logs", "runbook", "data_limits"}
    assert "log_ids" not in evidence["incident"]
    assert sum(evidence["statistics"]["levels"].values()) == incident.occurrences
    assert 0 < len(evidence["representative_logs"]) <= MAX_REPRESENTATIVE_LOGS
    assert {log["log_id"] for log in evidence["representative_logs"]} <= set(incident.log_ids)
    assert evidence["data_limits"]["log_lines_shared_with_ai"] <= MAX_REPRESENTATIVE_LOGS
    assert len(json.dumps(evidence)) < 40_000  # a compact prompt, not a log dump
    assert all(isinstance(fact, str) and fact for fact in evidence["facts"])


def test_related_errors_exclude_own_logs_and_cover_the_window(store):
    incident = _top_incident(store)
    related = build_evidence(incident, store)["related_errors"]
    assert incident.service in related["services_with_errors_in_window"]
    assert related["other_abnormal_logs_in_window"] >= 0
    assert all(o["incident_id"] != incident.incident_id for o in related["overlapping_incidents"])


def test_runbooks_exist_for_every_error_type_in_the_log(store):
    ingest(store)
    for error_type in {i.error_type for i in store.incidents.values()}:
        runbook = load_runbook(error_type)
        assert runbook and runbook["simulated"] is True and runbook["steps"]


def test_investigation_guardrails():
    insufficient = validate_investigation(InvestigationOutput.model_validate(
        {**INVESTIGATION_PAYLOAD, "insufficient_evidence": True, "confidence": 0.9}))
    assert insufficient.root_cause == INSUFFICIENT_EVIDENCE and insufficient.confidence <= 0.3
    no_facts = validate_investigation(InvestigationOutput.model_validate(
        {**INVESTIGATION_PAYLOAD, "evidence": [{"statement": "guess", "kind": "AI_INFERENCE"}], "confidence": 0.95}))
    assert no_facts.confidence <= 0.4 and no_facts.unknowns
    clamped = validate_investigation(InvestigationOutput.model_validate({**INVESTIGATION_PAYLOAD, "confidence": 7}))
    assert clamped.confidence == LOG_ONLY_MAX_CONFIDENCE


def test_runbook_source_is_verified():
    output = RecommendationOutput.model_validate(RECOMMENDATION_PAYLOAD)
    checked, notes = validate_recommendations(output, load_runbook("DATABASE_TIMEOUT"))
    assert [r.source for r in checked.recommendations] == ["runbook", "AI-generated", "AI-generated"]
    assert len(notes) == 1
    no_runbook, _ = validate_recommendations(output, None)
    assert all(r.source == "AI-generated" for r in no_runbook.recommendations)


def test_cross_service_spikes_are_found_in_the_real_log(store):
    ingest(store)
    spikes, median = spike_minutes(store.logs)
    hours = {(m.hour, m.minute) for m in spikes}
    # Exactly the two known cross-service spikes (09:30-09:32 and 10:43-10:46); isolated noisy minutes are not.
    assert hours == {(9, 30), (9, 31), (9, 32), (10, 43), (10, 44), (10, 45), (10, 46)}
    for incident in store.incidents.values():
        spike = build_evidence(incident, store)["related_errors"]["cross_service_spike"]
        assert spike["incident_events_inside_spike_minutes"] + spike["incident_events_outside_spike_minutes"] == incident.occurrences
