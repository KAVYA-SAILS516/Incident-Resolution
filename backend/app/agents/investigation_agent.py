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
from app.tools.application_tools import make_application_tools
from app.tools.incident_tools import make_incident_tools
from app.tools.log_tools import make_log_tools

# Note: no curly braces in instructions; ADK treats them as session-state placeholders.
INSTRUCTION = f"""You are the Investigation Agent investigating a production incident in an application whose
services, dependencies, criticality and documentation are described by get_application_context.
Deterministic code has already detected this incident, grouped its logs and set its workflow and priority.
Do not re-prioritise it. Your only job is to explain WHY it most likely happened.

How to work:
1. Call get_incident_details, get_log_statistics, get_error_timeline, get_related_errors,
   get_representative_logs, get_application_context, get_trace_correlation and get_relevant_failure_scenarios. Call them together in one turn.
2. Reason ONLY from what those tools return and the prompt; do not invent facts. Distinguish FACT (stated by a tool),
   AI_INFERENCE (your interpretation) and UNKNOWN (list it under unknowns).
3. Return your result.

Rules:
- Every evidence item has kind FACT or AI_INFERENCE.
  FACT means the tool output states it directly; put the tool name or log_id in source.
  AI_INFERENCE means your interpretation.
- Never invent metrics, deployments, configuration changes, dependencies, hosts or log lines. Dependencies,
  purpose and criticality may only be taken from get_application_context; if it returns null the application
  scan has no information about the service. Trace and metric facts only from get_trace_correlation; its
  telemetry is empty when the trace / metric backends are unavailable. There is no deployment history.
- Compare five kinds of evidence and say which each statement rests on: RUNTIME (logs), TRACE (spans, correlated
  services), METRIC (rates), CONFIGURATION (get_relevant_failure_scenarios: which scenarios the application's
  configuration has enabled, with the source file) and DOCUMENTATION (service docs, documented_in).
  Runtime evidence decides what happened; configuration never overrides it. A scenario with enabled=true is a
  probable cause only if its description is consistent with the runtime errors you see (same service, same kind
  of failure); then state it as the probable root cause, cite its source, and keep the runtime symptom as the
  FACT it explains. enabled=false is not a cause; enabled "UNKNOWN" or no scenario means the configuration is
  UNKNOWN - list that under unknowns and do not assume it. Never state a scenario is enabled unless the tool says so.
- Answer: what happened, which service failed, which dependency (if any) caused or contributed (use the
  correlated incidents and their relation), why it matters (criticality and dependents), the supporting
  evidence and plausible alternative causes (list them under unknowns).
- Use get_related_errors, especially cross_service_spike, to decide whether this incident is part of a wider
  event hitting several services at once, or a steady low-rate pattern outside any spike. State which.
- In get_related_errors, by_service_and_type counts individual log lines; only overlapping_incidents are
  incidents. Do not call a log count an incident.
- affected_services: services the evidence shows are affected; dependencies_involved: dependencies that caused or
  contributed, only if get_trace_correlation / get_application_context support it; alternative_causes: other
  plausible causes the evidence does not rule out. Use empty lists rather than guessing.
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
# The evidence is logs plus application / trace context (no change history), which can suggest but not
# prove a cause, so confidence is capped below certainty.
LOG_ONLY_MAX_CONFIDENCE = 0.85


def build_investigation_agent(evidence: dict, model=None) -> LlmAgent:
    return LlmAgent(
        name="investigation_agent",
        description="Determines the most likely root cause of one incident from aggregated log evidence.",
        model=model or get_model(),
        instruction=INSTRUCTION,
        tools=[*make_incident_tools(evidence), *make_log_tools(evidence), *make_application_tools(evidence)],
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


def previous_attempts(incident: Incident, store: JsonStore) -> str:
    """Earlier investigation and failed remediation attempts for this incident, so the model does not repeat them."""
    parts = []
    old = store.investigations.get(incident.incident_id)
    if old is not None:
        parts.append(f"Previous root-cause hypothesis (confidence {old.confidence:.0%}): {old.root_cause}")
    record = store.resolutions.get(incident.incident_id)
    if record is not None:
        attempts = {a.attempt_id: a for a in record.attempts}
        for v in record.verifications:
            if v.status == "FAILED" and v.attempt_id in attempts:
                parts.append(f"Remediation '{attempts[v.attempt_id].option_id}' did NOT recover the service: {v.reason}")
    return " ".join(parts)


def build_prompt(incident: Incident, history: str = "") -> str:
    return (
        f"Investigate incident {incident.incident_id}: {incident.title}. "
        f"Priority {incident.priority} (score {incident.priority_score:g}), workflow {incident.workflow} "
        f"({incident.workflow_criticality}), {incident.occurrences} occurrences. Use your tools, then return the result."
        + (f" Earlier attempts: {history}" if history else "")
    )


async def investigate(incident: Incident, store: JsonStore) -> InvestigationRecord:
    evidence = build_evidence(incident, store)
    agent = build_investigation_agent(evidence)
    run = await run_agent(agent, build_prompt(incident, previous_attempts(incident, store)), schema=InvestigationOutput)
    try:
        output = InvestigationOutput.model_validate(run.output)
    except ValidationError as exc:
        raise AgentFailed(f"investigation_agent returned an invalid result: {exc.errors()[:3]}") from exc
    output = validate_investigation(output)
    return InvestigationRecord(
        **output.model_dump(),
        incident_id=incident.incident_id,
        model=run.model,
        provider=run.provider,
        tools_called=run.tools_called,
        based_on_occurrences=incident.occurrences,
        duration_ms=run.duration_ms,
        created_at=datetime.now(timezone.utc),
    )
