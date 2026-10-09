"""Chat Agent (Google ADK): a dashboard-wide assistant that answers questions about incidents.

This is a third agent, beyond the Investigation and Resolution Decision agents that do the core detect-to-
recommend pipeline. It never investigates or recommends on its own - it only reads what those two agents
(and the deterministic rules) have already produced, through the same style of read-only tools.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.genai import types
from pydantic import ValidationError

from app.agents.runner import AgentFailed, get_model, run_agent
from app.schemas.chat import ChatOutput, ChatReply, ChatTurn
from app.state.store import JsonStore
from app.tools.chat_tools import make_chat_tools

MAX_HISTORY_TURNS = 10

# Note: no curly braces in instructions; ADK treats them as session-state placeholders.
INSTRUCTION = """You are the Chat Agent for an AI-native incident-resolution proof of concept, available
from its dashboard. You answer questions about incidents already detected, triaged, investigated and given
recommendations by deterministic rules and two other agents. You do not detect, investigate or recommend
anything yourself.

How to work:
1. Call list_incidents and/or get_dashboard_overview for questions across incidents, or get_incident_summary
   for a question about one specific incident (its id looks like INC-XXXXXXXX). Call tools as needed before
   answering; do not guess at data you have not fetched.
   Use get_application_knowledge for questions about the application itself (what it is, which services
   exist, what a service does or depends on, criticality, APIs, telemetry, failure scenarios).
2. Answer only from what the tools return.

Rules:
- Every incident has a status: "open" (not yet investigated), "investigated" (root cause found, no
  recommendations yet) or "recommendations_ready" (awaiting a human decision). This system has no "resolved"
  or "closed" state, so every incident that exists is still outstanding regardless of its status.
  When a user asks which incidents are "open", "active" or "outstanding" in the everyday sense, they mean
  "exist" - list incidents at any status, not only status="open". Only filter by the literal status value
  when the user clearly asks about analysis progress (e.g. "which ones haven't been investigated yet").
- Always list the incident id(s) your answer is actually about in referenced_incidents.
- If asked to take an action (restart, fix, roll back, approve, remediate, deploy), say plainly that you
  cannot execute anything from chat: actions are proposed and approved by a person in that incident's
  Resolution section, where they are guarded and simulated. Never say an action was performed.
- This system has no deployment/change history. If a question needs data the tools do not return, say it is
  not available here rather than guessing.
- Keep replies concise (under about 120 words) unless the user asks for more detail.
- Plain text only: no markdown, no code fences.
"""


def build_chat_agent(store: JsonStore, model=None) -> LlmAgent:
    return LlmAgent(
        name="chat_agent",
        description="Dashboard-wide assistant that answers questions about incidents from existing data.",
        model=model or get_model(),
        instruction=INSTRUCTION,
        tools=make_chat_tools(store),
        output_schema=ChatOutput,
        output_key="chat",
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
    )


def build_prompt(message: str, history: list[ChatTurn]) -> str:
    recent = history[-MAX_HISTORY_TURNS:]
    transcript = "\n".join(f"{'User' if t.role == 'user' else 'Assistant'}: {t.content}" for t in recent)
    prefix = f"Conversation so far:\n{transcript}\n\n" if transcript else ""
    return f"{prefix}User: {message}\nUse your tools, then return the result."


async def chat(message: str, history: list[ChatTurn], store: JsonStore) -> ChatReply:
    agent = build_chat_agent(store)
    run = await run_agent(agent, build_prompt(message, history), schema=ChatOutput)
    try:
        output = ChatOutput.model_validate(run.output)
    except ValidationError as exc:
        raise AgentFailed(f"chat_agent returned an invalid result: {exc.errors()[:3]}") from exc
    return ChatReply(
        **output.model_dump(),
        model=run.model,
        provider=run.provider,
        tools_called=run.tools_called,
        duration_ms=run.duration_ms,
    )
