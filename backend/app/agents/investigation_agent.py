"""Investigation Agent (Google ADK): explains WHY an incident happened, from deterministic evidence only."""

from __future__ import annotations

from datetime import datetime, timezone

from google.adk.agents import LlmAgent
from google.genai import types
from pydantic import ValidationError

from app.agents.runner import AgentFailed, get_model, run_agent
from app.schemas.incident import Incident
from app.schemas.investigation import INSUFFICIENT_EVIDENCE, InvestigationOutput, InvestigationRecord
from app.services.evidence_service import build_evidence
from app.state.store import JsonStore
from app.tools.incident_tools import make_incident_tools
from app.tools.log_tools import make_log_tools

# Note: no curly braces in instructions; ADK treats them as session-state placeholders.
INSTRUCTION = f"""You are the Investigation Agent in an incident-resolution proof of concept.
Deterministic code has already detected this incident, grouped its logs and set its workflow and priority.
Do not re-prioritise it. Your only job is to explain WHY it most likely happened.

How to work:
1. Call get_incident_details, get_log_statistics, get_error_timeline, get_related_errors and
   get_representative_logs. Call them together in one turn.
2. Reason only from what those tools return.
3. Return your result.

Rules:
- Every evidence item has kind FACT or AI_INFERENCE.
  FACT means the tool output states it directly; put the tool name or log_id in source.
  AI_INFERENCE means your interpretation.
- Never invent metrics, deployments, configuration changes, dependencies, hosts or log lines. The logs
  contain no infrastructure metrics, deployment history, traces or database-server logs.
- Use get_related_errors, especially cross_service_spike, to decide whether this incident is part of a wider
  event hitting several services at once, or a steady low-rate pattern outside any spike. State which.
- In get_related_errors, by_service_and_type counts individual log lines; only overlapping_incidents are
  incidents. Do not call a log count an incident.
- unknowns: what you would need to confirm the root cause but cannot see in the data.
- confidence is 0.0 to 1.0:
  0.7 to 0.85 only when the log messages themselves name the failing component (for example
  "Database connection timed out");
  0.4 to 0.6 when the cause is inferred from timing, correlation or latency patterns;
  0.3 or below when the evidence is insufficient.
- If the evidence cannot support any specific cause, set root_cause to exactly "{INSUFFICIENT_EVIDENCE}",
  set insufficient_evidence to true, keep confidence at 0.3 or below, and list what is missing in unknowns.
- Keep analysis under 150 words. Give 3 to 8 evidence items.
"""

INSUFFICIENT_MAX_CONFIDENCE = 0.3
NO_FACTS_MAX_CONFIDENCE = 0.4
# The evidence is application logs only (no metrics, traces or change history), which can suggest but not
# prove a cause, so confidence is capped below certainty.
LOG_ONLY_MAX_CONFIDENCE = 0.85


def build_investigation_agent(evidence: dict, model=None) -> LlmAgent:
    return LlmAgent(
        name="investigation_agent",
        description="Determines the most likely root cause of one incident from aggregated log evidence.",
        model=model or get_model(),
        instruction=INSTRUCTION,
        tools=[*make_incident_tools(evidence), *make_log_tools(evidence)],
        output_schema=InvestigationOutput,
        output_key="investigation",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
    )


def validate_investigation(output: InvestigationOutput) -> InvestigationOutput:
    """Deterministic guardrails on the model's answer."""
    result = output.model_copy(deep=True)
    result.confidence = min(LOG_ONLY_MAX_CONFIDENCE, max(0.0, float(result.confidence)))
    root = result.root_cause.strip()
    if not root or root.lower().startswith(INSUFFICIENT_EVIDENCE.lower()) or result.insufficient_evidence:
        result.root_cause = INSUFFICIENT_EVIDENCE
        result.insufficient_evidence = True
        result.confidence = min(result.confidence, INSUFFICIENT_MAX_CONFIDENCE)
    if not any(item.kind == "FACT" for item in result.evidence):
        result.confidence = min(result.confidence, NO_FACTS_MAX_CONFIDENCE)
        result.unknowns.append("The agent cited no log facts; the conclusion is unsupported inference.")
    return result


def build_prompt(incident: Incident) -> str:
    return (
        f"Investigate incident {incident.incident_id}: {incident.title}. "
        f"Priority {incident.priority} (score {incident.priority_score:g}), workflow {incident.workflow} "
        f"({incident.workflow_criticality}), {incident.occurrences} occurrences. Use your tools, then return the result."
    )


async def investigate(incident: Incident, store: JsonStore) -> InvestigationRecord:
    evidence = build_evidence(incident, store)
    agent = build_investigation_agent(evidence)
    run = await run_agent(agent, build_prompt(incident))
    try:
        output = InvestigationOutput.model_validate(run.output)
    except ValidationError as exc:
        raise AgentFailed(f"investigation_agent returned an invalid result: {exc.errors()[:3]}") from exc
    output = validate_investigation(output)
    return InvestigationRecord(
        **output.model_dump(),
        incident_id=incident.incident_id,
        model=run.model,
        tools_called=run.tools_called,
        based_on_occurrences=incident.occurrences,
        duration_ms=run.duration_ms,
        created_at=datetime.now(timezone.utc),
    )
