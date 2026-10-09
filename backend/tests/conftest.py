from __future__ import annotations

import os

# Tests must never read the developer's real backend/.env (it holds the real project / telemetry settings).
os.environ.setdefault("ENV_FILE", os.devnull)

import shutil
from pathlib import Path
from typing import AsyncGenerator

import pytest
from google.adk.models.base_llm import BaseLlm, LlmCapabilities
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.agents import auto_analysis, runner
from app.config.settings import Settings
from app.state import store as store_module

# A synthetic plain log kept ONLY as a parser/detector fixture. It is never loaded at runtime (FILE_LOG_INGEST is off).
REAL_LOG = Path(__file__).resolve().parent / "fixtures" / "sample_application.log"
LEGACY_FILE_LOG_MODULES = {"test_api", "test_chat", "test_evidence_service", "test_incident_detector", "test_log_parser"}


def log_line(ts: str, level: str = "ERROR", service: str = "payment-service", endpoint: str = "/api/v1/payments",
             status: int = 503, error_type: str = "DATABASE_TIMEOUT", host: str = "app-01", client: str = "client-1",
             env: str = "production-simulated", rt: int = 1200, message: str = "Database connection timed out") -> str:
    return (f"{ts} level={level} service={service} environment={env} host={host} request_id=req-{ts[-9:-1]} "
            f"client_id={client} method=POST endpoint={endpoint} status_code={status} response_time_ms={rt} "
            f'error_type={error_type} message="{message}"')


INVESTIGATION_PAYLOAD = {
    "root_cause": "Database connection pool exhaustion on payment-service",
    "confidence": 0.7,
    "evidence": [
        {"statement": "All events are DATABASE_TIMEOUT with HTTP 503", "kind": "FACT", "source": "get_log_statistics"},
        {"statement": "The database was likely saturated", "kind": "AI_INFERENCE", "source": ""},
    ],
    "analysis": "Timeouts cluster in a short window.",
    "unknowns": ["Database server metrics are not in the logs"],
    "insufficient_evidence": False,
}

RECOMMENDATION_PAYLOAD = {
    "recommendations": [
        {"title": "Check database connection pool saturation", "description": "Inspect pool usage.",
         "reason": "All events are DB timeouts.", "risk": "LOW", "source": "runbook"},
        {"title": "Page the vendor about a network outage", "description": "Open a vendor ticket.",
         "reason": "Speculative.", "risk": "MEDIUM", "source": "runbook"},
        {"title": "Add an alert on p95 latency", "description": "Create an alert.",
         "reason": "Latency rose before errors.", "risk": "LOW", "source": "AI-generated"},
    ]
}


CHAT_PAYLOAD = {
    "reply": "There is 1 P1 incident open: INC-AC837A74 (order-service, DATABASE_TIMEOUT).",
    "referenced_incidents": ["INC-AC837A74"],
}


class FakeLlm(BaseLlm):
    """Scripted stand-in for Gemini: first calls every tool, then answers via set_model_response."""

    model: str = "fake-llm"
    investigation: dict = INVESTIGATION_PAYLOAD
    recommendations: dict = RECOMMENDATION_PAYLOAD
    chat: dict = CHAT_PAYLOAD
    fail: bool = False  # simulate every call failing, to test error handling

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        if self.fail:
            raise RuntimeError("model unavailable")
        tools = list(llm_request.tools_dict)
        answered = any(p.function_response for c in llm_request.contents for p in (c.parts or []))
        if not answered:
            parts = [types.Part(function_call=types.FunctionCall(name=n, args={})) for n in tools if n != "set_model_response"]
        else:
            if "list_incidents" in tools:
                payload = self.chat
            elif "get_runbook" in tools:
                payload = self.recommendations
            else:
                payload = self.investigation
            parts = [types.Part(function_call=types.FunctionCall(name="set_model_response", args=payload))]
        yield LlmResponse(content=types.Content(role="model", parts=parts))


@pytest.fixture
def fake_llm():
    model = FakeLlm()
    runner.set_model_override(model)
    yield model
    runner.set_model_override(None)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Isolated data dir holding a copy of the real log (the original stays untouched)."""
    (tmp_path / "logs").mkdir()
    shutil.copyfile(REAL_LOG, tmp_path / "logs" / REAL_LOG.name)
    return tmp_path


@pytest.fixture
def store(data_dir: Path):
    yield store_module.configure_store(data_dir)
    store_module._store = None


@pytest.fixture(autouse=True)
def no_background_workflow(monkeypatch):
    """The background analysis workflow is off by default in tests, so an ingest never makes a real Vertex
    AI call. Tests that exercise it explicitly request `background_workflow`."""
    auto_analysis.reset()
    monkeypatch.setattr(auto_analysis, "settings", Settings(auto_analyze=False))
    yield
    auto_analysis.reset()


@pytest.fixture(autouse=True)
def legacy_file_logs(request, monkeypatch):
    """Plain-log-file import is off by default (runtime never reads local log files). Only the legacy parser /
    detector / API test modules switch it on, against the isolated fixture file."""
    if request.module.__name__.rsplit(".", 1)[-1] in LEGACY_FILE_LOG_MODULES:
        from app.api import logs as logs_api
        from app.services import ingestion

        enabled = Settings(file_log_ingest=True)
        monkeypatch.setattr(ingestion, "settings", enabled)
        monkeypatch.setattr(logs_api, "settings", enabled)


@pytest.fixture
def background_workflow(monkeypatch, fake_llm):
    """Turn the background workflow on, with the scripted model (deterministic, no network)."""
    monkeypatch.setattr(auto_analysis, "settings", Settings(auto_analyze=True, gcp_project="test-project"))
    return fake_llm
