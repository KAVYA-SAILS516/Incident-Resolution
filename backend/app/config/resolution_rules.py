"""Resolution policy: the guarded remediation actions and the rules that choose an execution mode.

Only actions listed in ACTIONS can ever be executed - the model/agents never supply a command. Thresholds
here are POC heuristics and are meant to be tuned.
"""

from __future__ import annotations

# action id -> description template, risk, reversibility, impact, and when it fits.
#   needs: a condition on the application knowledge ("always" | "failure_scenario" | "cache_dependency")
#   fit: how well the action fits when its signal words appear in the incident / when they do not.
ACTIONS: dict[str, dict] = {
    "restart_service": {
        "title": "Restart {service}", "prerequisites": ("{service} is restartable without data loss",),
        "expected_outcome": "Transient faults in {service} clear; error ratio returns to baseline within the verification window.",
        "description": "Restart the {service} service",
        "risk": "MEDIUM", "reversibility": "REVERSIBLE", "needs": "always",
        "impact": "Brief interruption of {service} while it restarts; clears transient faults.",
        "signals": (), "fit": (0.7, 0.7),
    },
    "rollback_configuration": {
        "title": "Roll back {service} configuration / disable failure flag",
        "prerequisites": ("a documented failure scenario or recent configuration change exists for {service}",),
        "expected_outcome": "The scenario switch for {service} returns to its default (off) and failures stop.",
        "description": "Roll back the most recent configuration / feature-flag change affecting {service}",
        "risk": "MEDIUM", "reversibility": "REVERSIBLE", "needs": "failure_scenario",
        "impact": "Reverts {service} to its default configuration; a documented failure scenario exists for it.",
        "signals": ("fail", "error", "unreachable", "invalid"), "fit": (0.85, 0.5),
    },
    "scale_service": {
        "title": "Scale out {service}", "prerequisites": ("capacity is available for another {service} instance",),
        "expected_outcome": "Added capacity absorbs load; timeouts and saturation errors in {service} fall.",
        "description": "Scale out the {service} service",
        "risk": "LOW", "reversibility": "REVERSIBLE", "needs": "always",
        "impact": "Adds capacity for {service}; no downtime.",
        "signals": ("timeout", "latency", "slow", "memory", "cpu", "limit", "pressure", "exhaust"), "fit": (0.75, 0.3),
    },
    "clear_cache": {
        "title": "Clear {service} cache", "prerequisites": ("{service} depends on a cache that can be rebuilt",),
        "expected_outcome": "Stale or corrupt cache entries are dropped and rebuilt; errors tied to the cache stop.",
        "description": "Clear the cache used by {service}",
        "risk": "LOW", "reversibility": "REVERSIBLE", "needs": "cache_dependency",
        "impact": "Cached data for {service} is rebuilt; short-lived latency increase.",
        "signals": ("cache", "stale", "evict"), "fit": (0.75, 0.3),
    },
}
CACHE_HINTS = ("cache", "valkey", "redis", "memcached")

# Execution-mode policy.
AUTO_MIN_CONFIDENCE = 0.80  # auto-execute needs at least this confidence ...
AUTO_ALLOWED_PRIORITIES = ("P3", "P4")  # ... and never on P1/P2 incidents
TAKEOVER_MAX_CONFIDENCE = 0.30  # at or below this a person should take over instead of approving
NO_INVESTIGATION_CONFIDENCE = 0.30

RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
# dependents of the target service -> blast radius level
BLAST_BANDS = [(4, "HIGH"), (2, "MEDIUM"), (0, "LOW")]

# Verification: recovered when the share of abnormal events among the service's events after the action is at
# most this fraction of the share during the incident. At least MIN_TRAFFIC_AFTER events of the service must
# have been seen after the action: with no traffic, a quiet log proves nothing.
RECOVERY_RATIO = 0.2
MIN_TRAFFIC_AFTER = 3
