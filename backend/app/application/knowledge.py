"""Reading ApplicationKnowledge: compact per-service context for the agents, and the deterministic
Knowledge answers. Nothing here knows any particular application - every fact comes from the scanned model.
"""

from __future__ import annotations

import re

from app.application.models import ApplicationKnowledge, ServiceInfo

UNKNOWN = "unknown"


def service_criticality(knowledge: ApplicationKnowledge | None, service: str | None) -> str | None:
    info = knowledge.service(service) if knowledge else None
    return info.criticality if info else None


def service_context(knowledge: ApplicationKnowledge | None, service: str | None) -> dict | None:
    """What the application says about one service (for triage, investigation and resolution)."""
    info = knowledge.service(service) if knowledge else None
    if info is None:
        return None
    return {
        "application": knowledge.application_name,
        "service": info.name,
        "purpose": info.purpose or UNKNOWN,
        "criticality": info.criticality or UNKNOWN,
        "criticality_source": info.criticality_source,
        "depends_on": info.dependencies,
        "depended_on_by": info.dependents,
        "apis": info.apis,
        "documentation": info.documentation_references,
        "documentation_notes": [{"path": d.path, "title": d.title, "summary": (d.summary or "")[:300]}
                                for d in knowledge.documentation if d.path in info.documentation_references][:5],
        "documented_failure_scenarios": [
            {"name": s.name, "description": s.description, "default_variant": s.default_variant}
            for s in knowledge.failure_scenarios if s.service == info.name
        ],
        "telemetry": info.telemetry_information,
    }


def _line(info: ServiceInfo) -> str:
    return f"{info.name} ({info.criticality or UNKNOWN})"


def _mentioned(knowledge: ApplicationKnowledge, question: str) -> list[ServiceInfo]:
    text = " " + re.sub(r"[^a-z0-9]+", " ", question.lower()) + " "
    found = [s for s in knowledge.services if f" {re.sub(r'[^a-z0-9]+', ' ', s.name.lower())} " in text]
    return sorted(found, key=lambda s: -len(s.name))[:1] if found else []


def answer_question(knowledge: ApplicationKnowledge | None, question: str) -> dict:
    """Answer an application question from the scanned model only. Returns {"answer", "facts"}."""
    if knowledge is None:
        return {"answer": "No application has been scanned yet.", "facts": []}
    q = question.lower()
    apps = [s for s in knowledge.services if s.kind == "application"]
    target = _mentioned(knowledge, question)
    facts: list[str] = []

    if target:
        s = target[0]
        if "depend" in q and ("on" in q or "need" in q or "call" in q):
            answer = f"{s.name} depends on: {', '.join(s.dependencies) or 'nothing found in the scanned files'}."
        elif "who" in q or "used by" in q or "depended" in q or "dependents" in q:
            answer = f"{s.name} is depended on by: {', '.join(s.dependents) or 'nothing found in the scanned files'}."
        elif "api" in q:
            answer = f"{s.name} APIs: {', '.join(s.apis) or 'none found in the scanned files'}."
        elif "critical" in q:
            answer = f"{s.name} criticality is {s.criticality or UNKNOWN}" + (f" (declared in {s.criticality_source})." if s.criticality_source else ".")
        elif "fail" in q or "scenario" in q:
            answer = f"{s.name} documented failure scenarios: {', '.join(s.failure_scenarios) or 'none documented'}."
        else:
            answer = f"{s.name}: {s.purpose or 'purpose not documented in the scanned files'}"
        facts = [f"{s.name}: criticality {s.criticality or UNKNOWN}", f"depends on {', '.join(s.dependencies) or 'none found'}"]
        return {"answer": answer, "facts": facts}

    if "critical" in q:
        crit = [s for s in apps if s.criticality in ("CRITICAL", "HIGH")]
        answer = "Services declared CRITICAL or HIGH: " + (", ".join(_line(s) for s in crit) or "none declared") + "."
    elif "failure" in q or "scenario" in q:
        answer = "Documented failure scenarios: " + (
            "; ".join(f"{s.name}" + (f" ({s.service})" if s.service else "") for s in knowledge.failure_scenarios) or "none") + "."
    elif "telemetry" in q or "logs" in q or "metrics" in q or "traces" in q:
        signals = knowledge.telemetry.get("signals", {})
        answer = ("Telemetry signals configured in the OpenTelemetry Collector: " + (", ".join(signals) or "none found")
                  + ". Collector config files: " + (", ".join(knowledge.telemetry.get("collector_configs", [])) or "none") + ".")
    elif "api" in q:
        answer = f"{len(knowledge.apis)} APIs found: " + ", ".join(a.name for a in knowledge.apis[:25]) + ("…" if len(knowledge.apis) > 25 else "") + "."
    elif "service" in q:
        answer = f"{len(apps)} application services: " + ", ".join(_line(s) for s in apps) + "."
    else:
        answer = f"{knowledge.application_name}: {knowledge.summary or 'no description found in the scanned documentation'}"
    return {"answer": answer, "facts": facts}
