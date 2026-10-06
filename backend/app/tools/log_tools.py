"""Log evidence tools for the agents. Each tool is bound to one incident's precomputed evidence, so the
model can only see aggregates and a bounded set of representative lines, never the whole log.
"""

from __future__ import annotations


def make_log_tools(evidence: dict) -> list:
    def get_log_statistics() -> dict:
        """Aggregated statistics for this incident: counts by level, status code, host, endpoint, method and
        client, message variants, and response times compared with the service's normal requests."""
        return {"facts": evidence["facts"], "statistics": evidence["statistics"], "baseline": evidence["baseline"]}

    def get_error_timeline() -> dict:
        """Events per minute for this incident, with peak rate and duration."""
        return evidence["timeline"]

    def get_related_errors() -> dict:
        """Other abnormal logs (other services and error types) in the same time window, plus overlapping
        incidents. Use it to decide whether this incident is isolated or part of a wider event."""
        return evidence["related_errors"]

    def get_representative_logs() -> dict:
        """A small, deterministic sample of this incident's log lines (first, last, slowest, one per host and
        per endpoint/status variant)."""
        return {"logs": evidence["representative_logs"], "data_limits": evidence["data_limits"]}

    return [get_log_statistics, get_error_timeline, get_related_errors, get_representative_logs]
