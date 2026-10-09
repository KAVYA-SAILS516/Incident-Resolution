"""Resolution Decision Agent (Google ADK): proposes what COULD be done. Nothing is executed."""

from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher

from google.adk.agents import LlmAgent
from google.genai import types
from pydantic import ValidationError

from app.agents.runner import AgentFailed, get_model, run_agent
from app.schemas.incident import Incident
from app.schemas.investigation import InvestigationRecord
from app.schemas.recommendation import RecommendationOutput, RecommendationRecord
from app.services.evidence_service import build_evidence
from app.state.store import JsonStore
from app.tools.application_tools import make_application_tools
from app.tools.incident_tools import make_incident_tools
from app.tools.resolution_tools import make_resolution_tools
from app.tools.runbook_tools import load_runbook, make_runbook_tools, runbook_step_titles

INSTRUCTION = """You are the Resolution Decision Agent in an incident-resolution proof of concept.
The incident was detected and prioritised by deterministic rules, and the Investigation Agent has analysed it.
Propose actions an on-call engineer could take. You only recommend; nothing is executed.

How to work:
1. Call get_incident_details, get_investigation_result, get_runbook, get_application_context and
   get_resolution_candidates. Call them together in one turn.
2. Return 3 to 5 recommendations, the most useful first.
3. Also return resolution_choice: pick recommended_option_id from get_resolution_candidates and explain why in
   reason, using the investigation, the service's criticality and dependencies, and any previously_failed options
   (never choose one of those; if no candidate is sensible return null). You may only choose an option_id that is
   listed there - there are no other executable actions, and you cannot change risk, blast radius or the
   execution_mode, which policy fixes. suggested_execution_mode may only ask for MORE caution than a candidate's.
   You cannot declare an incident resolved; telemetry verification does that.

Rules:
- If a runbook is available, prefer its steps where they fit the investigation. For such an action, copy the
  runbook step title exactly into title and set source to "runbook".
- Any action that is not a runbook step has source "AI-generated". If no runbook is available, every
  recommendation is "AI-generated".
- risk: LOW for read-only checks and diagnostics; MEDIUM for reversible changes such as config tweaks,
  scaling or restarting one instance; HIGH for failover, rollback, data changes or anything with broad impact.
- reason: tie each action to specific findings from the investigation (quote numbers or hosts where useful).
- If the investigation reported insufficient evidence or low confidence, put evidence-gathering and
  diagnostic actions first.
- Never claim an action has been performed.
"""

MAX_RECOMMENDATIONS = 6
RUNBOOK_TITLE_MATCH = 0.75


def build_recommendation_agent(evidence: dict, investigation: InvestigationRecord, error_type: str, model=None,
                               resolution_tools: list | None = None) -> LlmAgent:
    return LlmAgent(
        name="resolution_decision_agent",
        description="Recommends next actions for one investigated incident, grounded in runbooks where available.",
        model=model or get_model(),
        instruction=INSTRUCTION,
        tools=[*make_incident_tools(evidence, investigation), *make_runbook_tools(error_type),
               *make_application_tools(evidence), *(resolution_tools or [])],
        output_schema=RecommendationOutput,
        output_key="recommendations",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
    )


def _matches_runbook_step(title: str, steps: list[str]) -> bool:
    normalized = title.strip().lower()
    return any(SequenceMatcher(None, normalized, step.lower()).ratio() >= RUNBOOK_TITLE_MATCH for step in steps)


def validate_recommendations(output: RecommendationOutput, runbook: dict | None) -> tuple[RecommendationOutput, list[str]]:
    """Deterministic provenance check: "runbook" is only allowed for actions that are really runbook steps."""
    result = output.model_copy(deep=True)
    notes: list[str] = []
    steps = runbook_step_titles(runbook)
    for rec in result.recommendations:
        if rec.source == "runbook" and not _matches_runbook_step(rec.title, steps):
            rec.source = "AI-generated"
            notes.append(f"'{rec.title}' relabelled AI-generated: "
                         + ("no runbook exists for this error type." if runbook is None else "not a step in the runbook."))
    if len(result.recommendations) > MAX_RECOMMENDATIONS:
        notes.append(f"Trimmed to the first {MAX_RECOMMENDATIONS} recommendations.")
        result.recommendations = result.recommendations[:MAX_RECOMMENDATIONS]
    return result, notes


async def recommend(incident: Incident, investigation: InvestigationRecord, store: JsonStore) -> RecommendationRecord:
    evidence = build_evidence(incident, store)
    agent = build_recommendation_agent(evidence, investigation, incident.error_type,
                                       resolution_tools=make_resolution_tools(store, incident, investigation))
    prompt = (f"Recommend next actions for incident {incident.incident_id}: {incident.title} "
              f"({incident.priority}, {incident.workflow}). Use your tools, then return the result.")
    run = await run_agent(agent, prompt, schema=RecommendationOutput)
    try:
        output = RecommendationOutput.model_validate(run.output)
    except ValidationError as exc:
        raise AgentFailed(f"recommendation_agent returned an invalid result: {exc.errors()[:3]}") from exc
    if not output.recommendations:
        raise AgentFailed("recommendation_agent returned no recommendations")
    runbook = load_runbook(incident.error_type)
    output, notes = validate_recommendations(output, runbook)
    return RecommendationRecord(
        **output.model_dump(),
        incident_id=incident.incident_id,
        model=run.model,
        provider=run.provider,
        runbook_id=runbook["runbook_id"] if runbook else None,
        tools_called=run.tools_called,
        validation_notes=notes,
        duration_ms=run.duration_ms,
        created_at=datetime.now(timezone.utc),
    )
