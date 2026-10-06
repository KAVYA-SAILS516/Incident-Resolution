"""Runs a Google ADK agent once and returns its structured output.

Uses ADK's Runner with an in-memory session: each investigation or recommendation is one short,
self-contained run; results are persisted by the store, not in ADK sessions.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

from google import genai
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.config.settings import settings

APP_NAME = "incident_resolution_poc"
USER_ID = "poc-user"


class AgentUnavailable(RuntimeError):
    """Vertex AI is not configured."""


class AgentFailed(RuntimeError):
    """The agent ran but did not produce a valid result."""


@dataclass
class AgentRun:
    output: dict
    model: str
    tools_called: list[str] = field(default_factory=list)
    duration_ms: int = 0


_model_override: BaseLlm | None = None
_client: genai.Client | None = None


def set_model_override(model: BaseLlm | None) -> None:
    """Replace Gemini with another BaseLlm (tests use a scripted fake)."""
    global _model_override
    _model_override = model


def get_model() -> BaseLlm:
    global _client
    if _model_override is not None:
        return _model_override
    if not settings.llm_configured:
        raise AgentUnavailable("Vertex AI is not configured: set GOOGLE_CLOUD_PROJECT in backend/.env "
                               "and run `gcloud auth application-default login`.")
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=settings.gcp_project,
            location=settings.gcp_location,
            http_options=types.HttpOptions(
                timeout=settings.llm_timeout_seconds * 1000,
                retry_options=types.HttpRetryOptions(
                    attempts=settings.llm_max_attempts, http_status_codes=[408, 429, 500, 502, 503, 504]
                ),
            ),
        )
    return Gemini(model=settings.model, client=_client)


def _as_dict(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


async def run_agent(agent: LlmAgent, prompt: str) -> AgentRun:
    if not agent.output_key:
        raise ValueError("run_agent needs an agent with output_key")
    sessions = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
    session = await sessions.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=uuid.uuid4().hex)
    message = types.Content(role="user", parts=[types.Part(text=prompt)])
    tools_called: list[str] = []
    final_text: str | None = None
    started = time.perf_counter()

    async def consume() -> None:
        nonlocal final_text
        async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
            tools_called.extend(call.name for call in event.get_function_calls())
            if event.is_final_response() and event.content and event.content.parts:
                text = "".join(part.text or "" for part in event.content.parts)
                final_text = text or final_text

    try:
        await asyncio.wait_for(consume(), timeout=settings.llm_timeout_seconds * 2)
    except asyncio.TimeoutError as exc:
        raise AgentFailed(f"{agent.name} timed out") from exc
    except Exception as exc:  # model/API errors
        raise AgentFailed(f"{agent.name} failed: {exc}") from exc

    state = (await sessions.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session.id)).state
    output = _as_dict(state.get(agent.output_key)) or _as_dict(final_text)
    if output is None:
        raise AgentFailed(f"{agent.name} returned no structured output")
    model_name = agent.model if isinstance(agent.model, str) else getattr(agent.model, "model", "unknown")
    return AgentRun(
        output=output,
        model=model_name,
        tools_called=[name for name in tools_called if name != "set_model_response"],
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
