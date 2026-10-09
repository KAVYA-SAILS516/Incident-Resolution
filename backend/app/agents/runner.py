"""Runs a Google ADK agent once and returns its structured output.

This is the AI provider seam: agents never create model clients themselves, they get their model from
`get_model()`. The only real provider is Gemini on Vertex AI (AI_PROVIDER=vertex); tests inject a scripted model
with `set_model_override`, which is reported as provider "fake". There is no silent fallback from Vertex to a
fake: without configuration the call fails with a clear error.

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
from google.adk.agents.run_config import RunConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agents import ai_audit
from app.config.settings import settings

APP_NAME = "incident_resolution_poc"
USER_ID = "poc-user"
MAX_MODEL_CALLS = 15  # per agent run: tool turns + retries after invalid structured output (ADK default is 500)

NOT_CONFIGURED = ("Vertex AI is not configured. Set GOOGLE_CLOUD_PROJECT (and optionally GOOGLE_CLOUD_LOCATION, "
                  "VERTEX_MODEL) in backend/.env and authenticate with Application Default Credentials "
                  "(`gcloud auth application-default login`).")


class AgentUnavailable(RuntimeError):
    """Vertex AI is not configured or cannot be used with the current credentials."""


class AgentFailed(RuntimeError):
    """The agent ran but did not produce a valid result."""


@dataclass
class AgentRun:
    output: dict
    model: str
    provider: str = "vertex"
    tools_called: list[str] = field(default_factory=list)
    duration_ms: int = 0


_model_override: BaseLlm | None = None
_clients: dict[int, genai.Client] = {}  # one client per event loop: its async HTTP session is bound to the loop


def set_model_override(model: BaseLlm | None) -> None:
    """Replace Gemini with another BaseLlm (tests use a scripted fake)."""
    global _model_override
    _model_override = model


def provider_name() -> str:
    """The provider that serves the next call: "fake" only when a test injected a scripted model."""
    return "fake" if _model_override is not None else settings.ai_provider


def _loop_key() -> int:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


def get_model() -> BaseLlm:
    if _model_override is not None:
        return _model_override
    if settings.ai_provider != "vertex":
        raise AgentUnavailable(f"AI_PROVIDER={settings.ai_provider!r} is not supported; the only real provider is 'vertex'.")
    if not settings.llm_configured:
        raise AgentUnavailable(NOT_CONFIGURED)
    key = _loop_key()
    if key not in _clients:
        try:
            _clients[key] = genai.Client(
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
        except Exception as exc:  # missing/invalid Application Default Credentials, bad project or location
            raise AgentUnavailable(f"{NOT_CONFIGURED} ({type(exc).__name__}: {str(exc)[:160]})") from exc
    return Gemini(model=settings.model, client=_clients[key])


def explain_error(exc: Exception) -> str:
    """A short, user-meaningful reason for a failed model call (never contains credentials or prompts)."""
    text = f"{type(exc).__name__} {exc}"
    low = text.lower()
    if "llmcallslimit" in low:
        return f"the model did not return a valid structured result within {MAX_MODEL_CALLS} calls."
    if any(k in low for k in ("defaultcredentialserror", "unauthenticated", "invalid_grant", "reauthentication", "401")):
        return "Vertex AI authentication failed: run `gcloud auth application-default login`."
    if any(k in low for k in ("permission_denied", "403", "permission denied")):
        return "Vertex AI denied access: check the project, that the Vertex AI API is enabled and the account's roles."
    if any(k in low for k in ("resource_exhausted", "429", "quota")):
        return "Vertex AI quota or rate limit reached; try again later."
    if any(k in low for k in ("not_found", "404", "not found")):
        return f"Model '{settings.model}' is not available in {settings.gcp_location} for this project."
    if any(k in low for k in ("timeout", "timed out", "deadline")):
        return f"Vertex AI did not answer within {settings.llm_timeout_seconds}s."
    if any(k in low for k in ("connecterror", "connection", "network", "name resolution", "unreachable")):
        return "Vertex AI could not be reached (network error)."
    return f"Vertex AI call failed: {str(exc)[:200]}"


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


async def run_agent(agent: LlmAgent, prompt: str, schema=None) -> AgentRun:
    """Run the agent once. With `schema` (a Pydantic model) the output must validate or the call counts as failed."""
    if not agent.output_key:
        raise ValueError("run_agent needs an agent with output_key")
    sessions = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
    session = await sessions.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=uuid.uuid4().hex)
    message = types.Content(role="user", parts=[types.Part(text=prompt)])
    tools_called: list[str] = []
    final_text: str | None = None
    started = time.perf_counter()
    provider = provider_name()
    model_name = agent.model if isinstance(agent.model, str) else getattr(agent.model, "model", "unknown")

    def audit(success: bool, validation: str, error: str | None = None) -> None:
        ai_audit.record(agent=agent.name, provider=provider, model=model_name, success=success, validation=validation,
                        duration_ms=int((time.perf_counter() - started) * 1000), context_chars=len(prompt),
                        tools_called=[t for t in tools_called if t != "set_model_response"], error=error)

    async def consume() -> None:
        nonlocal final_text
        async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message,
                                          run_config=RunConfig(max_llm_calls=MAX_MODEL_CALLS)):
            tools_called.extend(call.name for call in event.get_function_calls())
            if event.is_final_response() and event.content and event.content.parts:
                text = "".join(part.text or "" for part in event.content.parts)
                final_text = text or final_text

    try:
        await asyncio.wait_for(consume(), timeout=settings.llm_timeout_seconds * 2)
    except asyncio.TimeoutError as exc:
        audit(False, "not_run", "timeout")
        raise AgentFailed(f"{agent.name} timed out after {settings.llm_timeout_seconds * 2}s") from exc
    except Exception as exc:  # model/API errors
        reason = explain_error(exc)
        audit(False, "not_run", reason)
        raise AgentFailed(f"{agent.name} failed: {reason}") from exc

    state = (await sessions.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session.id)).state
    output = _as_dict(state.get(agent.output_key)) or _as_dict(final_text)
    if output is None:
        audit(False, "no_output", "no structured output")
        raise AgentFailed(f"{agent.name} returned no structured output")
    if schema is not None:
        try:
            schema.model_validate(output)
        except Exception as exc:
            audit(False, "invalid", "output failed schema validation")
            raise AgentFailed(f"{agent.name} returned an invalid result: {str(exc)[:200]}") from exc
    audit(True, "valid" if schema is not None else "unchecked")
    return AgentRun(
        output=output,
        model=model_name,
        provider=provider,
        tools_called=[name for name in tools_called if name != "set_model_response"],
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
