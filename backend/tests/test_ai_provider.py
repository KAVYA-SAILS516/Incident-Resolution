"""The AI provider seam (Vertex AI / test provider), structured-output validation, and the policy layer that
keeps the model's resolution choice inside the registered, policy-approved options. No test calls Vertex AI."""

import asyncio
from typing import AsyncGenerator

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from app.agents import ai_audit, runner
from app.agents.investigation_agent import investigate
from app.agents.recommendation_agent import recommend
from app.config.settings import Settings
from app.schemas.resolution import RemediationAttempt, VerificationResult
from app.services import resolution_service
from tests.conftest import FakeLlm, RECOMMENDATION_PAYLOAD
from tests.test_resolution import act, events_after, investigation, no_pull, ready  # noqa: F401
from tests.test_telemetry_flow import incident_for


# --- provider seam ---
class DummyClient:
    pass


async def _client_for_new_loop():
    return runner.get_model().client


def test_vertex_provider_initialises_from_configuration(monkeypatch):
    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        return DummyClient()

    monkeypatch.setattr(runner, "settings", Settings(gcp_project="my-project", gcp_location="europe-west4", model="gemini-test"))
    monkeypatch.setattr(runner, "_clients", {})
    monkeypatch.setattr(runner.genai, "Client", fake_client)
    monkeypatch.setattr(runner, "Gemini", lambda model, client: type("G", (), {"model": model, "client": client})())
    model = runner.get_model()
    assert (created["vertexai"], created["project"], created["location"]) == (True, "my-project", "europe-west4")
    assert model.model == "gemini-test" and runner.provider_name() == "vertex"
    asyncio.run(asyncio.sleep(0))  # a different event loop gets its own client, never a closed one
    assert asyncio.run(_client_for_new_loop()) is not model.client


def test_missing_vertex_configuration_fails_clearly_without_a_fake(monkeypatch):
    monkeypatch.setattr(runner, "settings", Settings(gcp_project=None))
    with pytest.raises(runner.AgentUnavailable, match="Vertex AI is not configured.*Application Default Credentials"):
        runner.get_model()


def test_unsupported_provider_and_credential_errors(monkeypatch):
    monkeypatch.setattr(runner, "settings", Settings(ai_provider="openai", gcp_project="p"))
    with pytest.raises(runner.AgentUnavailable, match="not supported"):
        runner.get_model()
    monkeypatch.setattr(runner, "settings", Settings(gcp_project="p"))
    monkeypatch.setattr(runner, "_clients", {})

    def no_credentials(**_):
        raise RuntimeError("Your default credentials were not found")

    monkeypatch.setattr(runner.genai, "Client", no_credentials)
    with pytest.raises(runner.AgentUnavailable, match="not configured"):
        runner.get_model()


def test_test_provider_is_labelled_fake(fake_llm):
    assert runner.provider_name() == "fake"


def test_error_messages_are_meaningful():
    assert "authentication" in runner.explain_error(RuntimeError("DefaultCredentialsError: no creds"))
    assert "quota" in runner.explain_error(RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert "not available" in runner.explain_error(RuntimeError("404 NOT_FOUND model"))
    assert "denied" in runner.explain_error(RuntimeError("403 PERMISSION_DENIED"))
    assert "network" in runner.explain_error(RuntimeError("ConnectError: connection refused"))


# --- investigation through the provider ---
def test_investigation_records_provider_and_audit(ready, fake_llm):  # noqa: F811
    store, incident = ready
    record = asyncio.run(investigate(incident, store))
    assert record.provider == "fake"  # never labelled as Gemini/Vertex
    entry = ai_audit.recent()[-1]
    assert (entry["agent"], entry["provider"], entry["success"], entry["validation"]) == ("investigation_agent", "fake", True, "valid")
    assert entry["context_chars"] > 0 and "prompt" not in entry and "output" not in entry
    assert {"affected_services", "dependencies_involved", "alternative_causes"} <= set(record.model_dump())


def test_structured_investigation_output_is_validated(ready, fake_llm):  # noqa: F811
    store, incident = ready
    fake_llm.investigation = {"root_cause": "x"}  # missing required fields
    with pytest.raises(runner.AgentFailed):
        asyncio.run(investigate(incident, store))
    entry = ai_audit.recent()[-1]
    assert entry["success"] is False and entry["validation"] in ("invalid", "no_output", "not_run")
    assert store.investigations[incident.incident_id].model == "fake" and not store.investigations[incident.incident_id].tools_called  # unchanged: nothing new was stored


def test_previous_failed_attempts_reach_the_prompt(ready):  # noqa: F811
    from app.agents.investigation_agent import previous_attempts

    store, incident = ready
    attempt = act(store, incident)
    record = store.resolutions[incident.incident_id]
    record.verifications.append(VerificationResult(attempt_id=attempt.attempt_id, status="FAILED", reason="still failing",
                                                   checked_at=attempt.executed_at))
    text = previous_attempts(incident, store)
    assert "Previous root-cause hypothesis" in text and attempt.option_id in text and "still failing" in text


class SlowLlm(FakeLlm):
    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        await asyncio.sleep(5)
        async for item in super().generate_content_async(llm_request, stream):
            yield item


class AuthFailLlm(FakeLlm):
    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        raise RuntimeError("DefaultCredentialsError: reauthentication is needed")
        yield  # pragma: no cover


def test_timeout_and_authentication_failures_are_reported(ready, monkeypatch):  # noqa: F811
    store, incident = ready
    runner.set_model_override(AuthFailLlm())
    try:
        with pytest.raises(runner.AgentFailed, match="authentication failed"):
            asyncio.run(investigate(incident, store))
        monkeypatch.setattr(runner, "settings", Settings(llm_timeout_seconds=0))
        runner.set_model_override(SlowLlm())
        with pytest.raises(runner.AgentFailed, match="timed out"):
            asyncio.run(investigate(incident, store))
    finally:
        runner.set_model_override(None)
    assert [c["success"] for c in ai_audit.recent()[-2:]] == [False, False]


# --- resolution: the model chooses, policy decides ---
def choice(option_id, mode=None, reason="Because the evidence says so."):
    payload = {**RECOMMENDATION_PAYLOAD, "resolution_choice": {
        "recommended_option_id": option_id, "reason": reason, "suggested_execution_mode": mode,
        "option_notes": [{"option_id": "no-such-option", "note": "x"}]}}
    return payload


def run_recommend(store, incident, fake_llm, payload):
    fake_llm.recommendations = payload
    inv = store.investigations[incident.incident_id]
    store.save_recommendations(asyncio.run(recommend(incident, inv, store)))
    return resolution_service.refresh(store, incident.incident_id)


def test_candidates_tool_is_what_the_model_sees(ready, fake_llm):  # noqa: F811
    store, incident = ready
    run_recommend(store, incident, fake_llm, choice("restart_service:payment"))
    assert "get_resolution_candidates" in store.recommendations[incident.incident_id].tools_called


def test_ai_choice_leads_among_valid_options(ready, fake_llm):  # noqa: F811
    store, incident = ready
    record = run_recommend(store, incident, fake_llm, choice("restart_service:payment"))
    assert record.recommended_option_id == "restart_service:payment"
    assert record.reasoned_by == "ai+policy" and "Because the evidence" in record.ai_reason
    assert record.decision == "HUMAN_APPROVAL"  # policy, not the model, fixed the mode
    assert any("unknown option" in n for n in record.validation_notes)


def test_model_cannot_invent_remediation_actions(ready, fake_llm):  # noqa: F811
    store, incident = ready
    record = run_recommend(store, incident, fake_llm, choice("format_disk:payment"))
    assert "format_disk:payment" not in {o.option_id for o in record.options}
    assert record.reasoned_by == "policy" and record.recommended_option_id == "rollback_configuration:payment"
    assert any("not an allowed candidate" in n for n in record.validation_notes)


def test_model_cannot_pick_a_failed_option_again(ready, fake_llm):  # noqa: F811
    from datetime import timedelta
    from app.services import verification
    from app.telemetry import otel

    store, incident = ready
    attempt = act(store, incident)
    otel.save_events(store.data_dir, events_after(attempt, "payment", "ERROR", 12))
    verification.verify(store, incident.incident_id, now=attempt.executed_at + timedelta(seconds=120), pull=no_pull)
    failed = store.resolutions[incident.incident_id].failed_options[0]
    record = run_recommend(store, incident, fake_llm, choice(failed))
    assert failed not in {o.option_id for o in record.options}
    assert record.recommended_option_id != failed and record.reasoned_by == "policy"


def test_policy_blocks_unsafe_auto_execute_but_accepts_more_caution(ready, fake_llm):  # noqa: F811
    store, incident = ready
    record = run_recommend(store, incident, fake_llm, choice("restart_service:payment", mode="AUTO_EXECUTE"))
    assert record.decision == "HUMAN_APPROVAL" and any("policy requires HUMAN_APPROVAL" in n for n in record.validation_notes)
    record = run_recommend(store, incident, fake_llm, choice("restart_service:payment", mode="HUMAN_TAKEOVER"))
    assert record.decision == "HUMAN_TAKEOVER"  # the model may ask for stricter handling


def test_all_options_failed_still_ends_in_human_takeover(ready, fake_llm):  # noqa: F811
    store, incident = ready
    record = store.resolutions.get(incident.incident_id) or resolution_service.refresh(store, incident.incident_id)
    record.failed_options = ["rollback_configuration:payment", "restart_service:payment"]
    store.save_resolution(record)
    final = run_recommend(store, incident, fake_llm, choice("restart_service:payment"))
    assert final.decision == "HUMAN_TAKEOVER" and final.recommended_option_id is None
