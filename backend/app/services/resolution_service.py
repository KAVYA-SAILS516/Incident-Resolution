"""Resolution Decision: turns the investigation + application knowledge into a small set of feasible,
guarded options, picks one to recommend and decides who acts (auto / human approval / human takeover).

Deterministic by design: options only come from the registered action catalogue (config/resolution_rules),
so nothing the model says can introduce a new executable action. The Resolution Decision Agent's free-text
suggestions stay advisory; this service decides what the system may actually do.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.models import ApplicationKnowledge
from app.config import resolution_rules as rules
from app.config.settings import settings
from app.schemas.incident import Incident
from app.schemas.investigation import InvestigationRecord
from app.schemas.resolution import ExecutionMode, ResolutionOption, ResolutionRecord
from app.state.store import JsonStore


def _blast(dependents: int) -> str:
    return next(level for minimum, level in rules.BLAST_BANDS if dependents >= minimum)


def _applies(need: str, knowledge: ApplicationKnowledge, service: str) -> bool:
    info = knowledge.service(service)
    if info is None:
        return False
    if need == "failure_scenario":
        return bool(info.failure_scenarios)
    if need == "cache_dependency":
        return any(h in dep for dep in info.dependencies for h in rules.CACHE_HINTS)
    return info.kind == "application"


def execution_mode(incident: Incident, risk: str, reversibility: str, confidence: float) -> ExecutionMode:
    if confidence <= rules.TAKEOVER_MAX_CONFIDENCE or risk == "HIGH":
        return "HUMAN_TAKEOVER"
    if (risk == "LOW" and reversibility == "REVERSIBLE" and confidence >= rules.AUTO_MIN_CONFIDENCE
            and incident.priority in rules.AUTO_ALLOWED_PRIORITIES and incident.service_criticality != "CRITICAL"):
        return "AUTO_EXECUTE"
    return "HUMAN_APPROVAL"


def _targets(incident: Incident, knowledge: ApplicationKnowledge) -> list[str]:
    """The failing service, plus dependencies that are failing in the same traces (possible contributors)."""
    targets = [incident.service]
    targets += [c["service"] for c in incident.correlated_incidents
                if c.get("relation") == "dependency" and c["service"] not in targets]
    return targets


def build_options(incident: Incident, investigation: InvestigationRecord | None, knowledge: ApplicationKnowledge | None,
                  failed_actions: set[str]) -> list[ResolutionOption]:
    if knowledge is None:
        return []
    base = investigation.confidence if investigation else rules.NO_INVESTIGATION_CONFIDENCE
    text = " ".join([incident.error_type, incident.title] + ([investigation.root_cause] if investigation else [])).lower()
    options = []
    for target in _targets(incident, knowledge):
        info = knowledge.service(target)
        for action, spec in rules.ACTIONS.items():
            option_id = f"{action}:{target}"
            if option_id in failed_actions or not _applies(spec["needs"], knowledge, target):
                continue
            matched = any(word in text for word in spec["signals"]) if spec["signals"] else True
            fit = spec["fit"][0] if matched else spec["fit"][1]
            confidence = round(min(1.0, base * fit), 2)
            dependents = len(info.dependents) if info else 0
            level = _blast(dependents)
            shown = ", ".join(info.dependents[:4]) if info and info.dependents else "none"
            options.append(ResolutionOption(
                option_id=option_id, action=action, target_service=target,
                title=spec["title"].format(service=target), description=spec["description"].format(service=target),
                prerequisites=[p.format(service=target) for p in spec["prerequisites"]],
                expected_outcome=spec["expected_outcome"].format(service=target), risk=spec["risk"],
                impact=spec["impact"].format(service=target),
                blast_radius=f"{target} and {dependents} dependent service(s): {shown}", blast_radius_level=level,
                reversibility=spec["reversibility"], confidence=confidence,
                execution_mode=execution_mode(incident, spec["risk"], spec["reversibility"], confidence),
                rationale=(f"{'Matches' if matched else 'Does not clearly match'} the observed signals "
                           f"({incident.error_type}); investigation confidence {base:.0%}."),
            ))
    return sorted(options, key=lambda o: (-o.confidence, rules.RISK_ORDER[o.risk], o.option_id))


MODE_STRICTNESS = {"AUTO_EXECUTE": 0, "HUMAN_APPROVAL": 1, "HUMAN_TAKEOVER": 2}


def apply_ai_choice(options: list[ResolutionOption], recommendation) -> tuple[list[ResolutionOption], str | None, str | None, list[str]]:
    """Let the model's pick (if any) lead, but only among the policy-approved candidates.

    Returns (options, ai_reason, suggested_mode, notes). An id that is not a current candidate (invented, already
    failed, or not allowed for the service) is rejected and the policy ranking stays in charge."""
    choice = getattr(recommendation, "resolution_choice", None)
    if choice is None:
        return options, None, None, []
    by_id = {o.option_id: o for o in options}
    notes = [f"Ignored note for unknown option '{n.option_id}'." for n in choice.option_notes if n.option_id not in by_id]
    for note in choice.option_notes:
        if note.option_id in by_id:
            by_id[note.option_id].rationale = f"{by_id[note.option_id].rationale} AI: {note.note}"[:600]
    picked = by_id.get(choice.recommended_option_id) if choice.recommended_option_id else None
    if picked is None:
        if choice.recommended_option_id:
            notes.append(f"AI recommended '{choice.recommended_option_id}', which is not an allowed candidate; "
                         "the policy ranking was used instead.")
        return options, None, None, notes
    ordered = [picked] + [o for o in options if o is not picked]
    return ordered, choice.reason or None, choice.suggested_execution_mode, notes


def _decide(options: list[ResolutionOption], attempts: int, incident: Incident) -> tuple[str | None, ExecutionMode, str, str]:
    if attempts >= settings.remediation_max_attempts:
        return (None, "HUMAN_TAKEOVER",
                f"{attempts} remediation attempt(s) did not recover the incident; escalating to a person.",
                "A person takes over the incident.")
    if not options:
        return (None, "HUMAN_TAKEOVER", "No registered remediation action applies to this service.",
                "A person investigates and resolves the incident manually.")
    best = options[0]
    reason = (f"Recommended '{best.description}': confidence {best.confidence:.0%}, {best.risk} risk, "
              f"{best.reversibility.lower()}, {incident.priority} incident")
    if best.execution_mode == "AUTO_EXECUTE":
        return best.option_id, best.execution_mode, reason + "; policy allows automatic execution.", "Execute the action, then verify recovery."
    if best.execution_mode == "HUMAN_APPROVAL":
        return best.option_id, best.execution_mode, reason + "; policy requires a person to approve.", "A person approves or rejects the action."
    return best.option_id, best.execution_mode, reason + "; confidence/risk too poor for approval.", "A person takes over the incident."


def refresh(store: JsonStore, incident_id: str) -> ResolutionRecord:
    """(Re)build the resolution for an incident, keeping attempts, verifications and failed options."""
    incident = store.incidents[incident_id]
    previous = store.resolutions.get(incident_id)
    investigation = store.investigations.get(incident_id)
    failed = set(previous.failed_options) if previous else set()
    attempts = previous.attempts if previous else []
    options = build_options(incident, investigation, store.application, failed)
    options, ai_reason, suggested, notes = apply_ai_choice(options, store.recommendations.get(incident_id))
    recommended, decision, reason, next_step = _decide(options, len(failed), incident)
    if recommended and suggested and MODE_STRICTNESS[suggested] > MODE_STRICTNESS[decision]:
        decision = suggested  # the model may ask for more caution than the policy, never for less
        reason += f"; the AI asked for stricter handling ({suggested})."
    elif recommended and suggested and MODE_STRICTNESS[suggested] < MODE_STRICTNESS[decision]:
        notes.append(f"AI suggested {suggested}, but policy requires {decision}.")
    if ai_reason and recommended:
        reason += f" AI reasoning: {ai_reason}"
    now = datetime.now(timezone.utc)
    state = previous.state if previous else "proposed"
    if state == "resolved":
        decision, next_step = previous.decision, "Resolved: recovery was verified from telemetry."
    elif decision == "HUMAN_TAKEOVER" and failed:
        state = "human_takeover"
    record = ResolutionRecord(
        incident_id=incident_id, options=options, recommended_option_id=recommended, decision=decision,
        decision_reason=reason, state=state, next_step=next_step, based_on_investigation=investigation is not None,
        attempts=attempts, verifications=previous.verifications if previous else [], failed_options=sorted(failed),
        reasoned_by="ai+policy" if ai_reason and recommended else "policy", ai_reason=ai_reason if recommended else None,
        validation_notes=notes,
        created_at=previous.created_at if previous else now, updated_at=now,
    )
    store.save_resolution(record)
    return record
