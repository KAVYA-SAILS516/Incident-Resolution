"""Application knowledge tools for the agents: what the scanned application says about the failing service,
how the incident correlates across services (trace ids) and what traces / metrics show. Read-only, bound to
one incident's precomputed evidence."""

from __future__ import annotations


def make_application_tools(evidence: dict) -> list:
    def get_application_context() -> dict:
        """What the scanned application documents about the failing service: purpose, criticality, the services
        it depends on and those that depend on it, its APIs, documentation files and documented failure
        scenarios. Null means the application scan found nothing; never assume missing facts."""
        return {"service": evidence["application_context"], "dependencies": evidence["correlation"]["dependency_contexts"]}

    def get_trace_correlation() -> dict:
        """Trace ids of this incident, the other services failing inside the same traces, correlated incidents
        (with their relation: dependency, dependent or same_trace) and, when available, trace span summaries and
        service metrics (request / error rate)."""
        return {"correlation": {k: v for k, v in evidence["correlation"].items() if k != "dependency_contexts"},
                "telemetry": evidence["telemetry"]}

    def get_relevant_failure_scenarios(service: str | None = None) -> dict:
        """Failure scenarios (feature flags / fault-injection switches) that the application defines for the failing
        service, its dependencies and services failing in the same traces, with whether the application's
        configuration currently has each ENABLED (true / false / "UNKNOWN"), the description, and the sources
        (configuration file, documents that mention it). This is configuration evidence only: an enabled scenario
        is a candidate explanation that must be consistent with the runtime errors, not proof of cause. Pass
        `service` to restrict the list to one service."""
        found = evidence["failure_scenarios"]
        if service:
            found = [s for s in found if s["service"] == service]
        return {"scenarios": found,
                "note": "Empty means the application scan found no documented failure scenario for these services; "
                        "'UNKNOWN' means the configuration did not say whether it is on."}

    return [get_application_context, get_trace_correlation, get_relevant_failure_scenarios]
