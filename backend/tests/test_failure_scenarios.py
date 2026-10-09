"""Configuration / failure-scenario evidence for investigations. The tool only reports what the application's own
files say; the root-cause reasoning stays with the model."""

import asyncio
import json
from typing import AsyncGenerator, ClassVar

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.agents import runner
from app.agents.investigation_agent import INSTRUCTION, investigate
from app.application import parser
from app.application.discovery import discover_application
from app.services.evidence_service import build_evidence
from app.services.ingestion import ingest
from app.telemetry import otel
from tests.conftest import FakeLlm
from tests.mini_shop import make_app
from tests.test_telemetry_flow import failing_checkout, incident_for


def flags(variant: str | None, state: str = "ENABLED") -> str:
    flag = {"description": "Fail payment service charge requests", "state": state,
            "variants": {"off": 0, "on": 1}}
    if variant is not None:
        flag["defaultVariant"] = variant
    return json.dumps({"flags": {"paymentFailure": flag}})


def setup(store, tmp_path, flag_text: str | None, documented: bool = True):
    app = make_app(tmp_path)
    if flag_text is None:
        (app / "src" / "payment" / "flags.json").unlink()
    else:
        (app / "src" / "payment" / "flags.json").write_text(flag_text, encoding="utf-8")
    if documented:
        (app / "docs" / "architecture.md").write_text("# Architecture\n\nSet paymentFailure to make charges fail.\n", encoding="utf-8")
    store.save_application(discover_application("Shop", str(app)))
    otel.save_events(store.data_dir, failing_checkout())
    ingest(store)
    return app, incident_for(store, "payment")


def scenarios(store, incident):
    return build_evidence(incident, store)["failure_scenarios"]


# --- flag parsing ---
def test_flag_enabled_semantics():
    assert parser.flag_enabled({"variants": {"off": 0, "on": 1}, "defaultVariant": "on"}) is True
    assert parser.flag_enabled({"variants": {"off": False, "on": True}, "defaultVariant": "off"}) is False
    assert parser.flag_enabled({"variants": {"10%": 0.1, "off": 0}, "defaultVariant": "10%"}) is True
    assert parser.flag_enabled({"variants": {"off": 0, "on": 1}, "defaultVariant": "on", "state": "DISABLED"}) is False
    assert parser.flag_enabled({"variants": {"off": 0}}) is None  # no active variant stated
    assert parser.flag_enabled({}) is None


# --- evidence ---
def test_enabled_scenario_is_reported_with_sources(store, tmp_path):
    _, incident = setup(store, tmp_path, flags("on"))
    (found,) = scenarios(store, incident)
    assert (found["scenario"], found["service"], found["enabled"], found["active_variant"]) == ("paymentFailure", "payment", True, "on")
    assert "src/payment/flags.json" in found["source"] and "just now" in found["source"]
    assert found["documented_in"] == ["docs/architecture.md"]
    assert found["description"] and found["relevance"] == "the failing service"


def test_disabled_scenario_is_reported_as_disabled(store, tmp_path):
    _, incident = setup(store, tmp_path, flags("off"))
    assert scenarios(store, incident)[0]["enabled"] is False


def test_state_is_read_fresh_not_from_the_last_scan(store, tmp_path):
    app, incident = setup(store, tmp_path, flags("off"))
    assert scenarios(store, incident)[0]["enabled"] is False
    (app / "src" / "payment" / "flags.json").write_text(flags("on"), encoding="utf-8")  # operator flips it after the scan
    assert scenarios(store, incident)[0]["enabled"] is True


def test_unknown_configuration_stays_unknown(store, tmp_path):
    _, incident = setup(store, tmp_path, flags(None))  # the file does not say which variant is active
    assert scenarios(store, incident)[0]["enabled"] == "UNKNOWN"


def test_no_scenario_means_empty_not_invented(store, tmp_path):
    _, incident = setup(store, tmp_path, None)
    assert scenarios(store, incident) == []


def test_only_relevant_services_are_included(store, tmp_path):
    app, incident = setup(store, tmp_path, flags("on"))
    other = app / "src" / "email"
    other.mkdir()
    (other / "flags.json").write_text(json.dumps({"flags": {"emailFailure": {"defaultVariant": "on", "variants": {"on": 1}}}}), encoding="utf-8")
    store.save_application(discover_application("Shop", str(app)))
    ingest(store)
    names = {s["scenario"] for s in scenarios(store, incident_for(store, "payment"))}
    assert names == {"paymentFailure"}  # email is neither the failing service nor related to it


def test_rereading_cannot_escape_the_application_root(store, tmp_path):
    _, incident = setup(store, tmp_path, flags("on"))
    store.application.failure_scenarios[0].source = "../outside.json"
    found = scenarios(store, incident)[0]
    assert "could not be re-read" in found["source"]


# --- the model gets the evidence and the instructions ---
def test_instruction_demands_evidence_comparison_without_config_overriding_runtime():
    for phrase in ("RUNTIME", "TRACE", "METRIC", "CONFIGURATION", "DOCUMENTATION", "never overrides",
                   "UNKNOWN", "get_relevant_failure_scenarios"):
        assert phrase in INSTRUCTION


class ConfigReadingLlm(FakeLlm):
    """Test double that concludes only from what get_relevant_failure_scenarios returned to it, so the tests prove
    the evidence reaches the model and a model reading it can (or cannot) reach the scenario. Real reasoning is Gemini's."""

    seen: ClassVar[list] = []  # shared with the test: pydantic copies plain class attributes per instance

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        responses = {p.function_response.name: p.function_response.response
                     for c in llm_request.contents for p in (c.parts or []) if p.function_response}
        if "get_relevant_failure_scenarios" in responses:
            found = responses["get_relevant_failure_scenarios"].get("scenarios", [])
            self.seen.append(found)
            enabled = [s for s in found if s["enabled"] is True]
            unknown = [s for s in found if s["enabled"] == "UNKNOWN"]
            cause = (f"The {enabled[0]['scenario']} scenario is enabled ({enabled[0]['source']})" if enabled
                     else "Payment requests are failing with an invalid token; the cause is not established")
            self.investigation = {
                "root_cause": cause, "confidence": 0.7 if enabled else 0.5,
                "evidence": [{"statement": "payment errors", "kind": "FACT", "source": "get_log_statistics"}],
                "analysis": "x", "unknowns": [f"{s['scenario']} state is UNKNOWN" for s in unknown], "insufficient_evidence": False}
        async for item in super().generate_content_async(llm_request, stream):
            yield item


def investigate_with_reader(store, incident):
    ConfigReadingLlm.seen = []
    runner.set_model_override(ConfigReadingLlm())
    try:
        return asyncio.run(investigate(incident, store))
    finally:
        runner.set_model_override(None)


def test_payment_errors_alone_do_not_produce_the_flag_as_cause(store, tmp_path):
    _, incident = setup(store, tmp_path, None)  # runtime errors, no scenario known to the application
    record = investigate_with_reader(store, incident)
    assert "paymentFailure" not in record.root_cause and "get_relevant_failure_scenarios" in record.tools_called
    assert ConfigReadingLlm.seen == [[]]


def test_enabled_scenario_with_docs_lets_the_model_name_it_and_cite_the_source(store, tmp_path):
    _, incident = setup(store, tmp_path, flags("on"))
    record = investigate_with_reader(store, incident)
    assert "paymentFailure" in record.root_cause and "src/payment/flags.json" in record.root_cause
    assert ConfigReadingLlm.seen[0][0]["documented_in"] == ["docs/architecture.md"]


def test_disabled_scenario_is_not_reported_as_the_cause(store, tmp_path):
    _, incident = setup(store, tmp_path, flags("off"))
    record = investigate_with_reader(store, incident)
    assert "paymentFailure" not in record.root_cause


def test_unknown_configuration_is_carried_as_unknown(store, tmp_path):
    _, incident = setup(store, tmp_path, flags(None))
    record = investigate_with_reader(store, incident)
    assert "paymentFailure" not in record.root_cause and any("UNKNOWN" in u for u in record.unknowns)
