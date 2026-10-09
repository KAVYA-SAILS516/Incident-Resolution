"""Priority scoring rules (a POC heuristic, not an industry standard).

score = 100 * sum(weight * factor), each factor normalised to 0..1. Priority bands come from thresholds.
Business criticality carries the most weight, so a payment incident outranks a reporting incident with
otherwise identical signals.
"""

from __future__ import annotations

import os

# Criticality -> points (spec), normalised by the max when scoring.
CRITICALITY_SCORES: dict[str, int] = {"CRITICAL": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "UNKNOWN": 1}

WEIGHTS: dict[str, float] = {
    "workflow_criticality": 0.40,
    "severity": 0.15,
    "error_class": 0.15,
    "volume": 0.15,
    "blast_radius": 0.10,
    "burst_rate": 0.05,
}

# Share of the score given to the application's own criticality for the failing service (e.g. the
# `service.criticality` an application declares). It is used only when the service is known to a scanned
# application; the other weights are then scaled down so the total stays 1.0. Incidents from plain log files
# keep the original weights above.
SERVICE_CRITICALITY_WEIGHT = 0.20

# Highest log level seen in the incident.
SEVERITY_SCORES: dict[str, float] = {"CRITICAL": 1.0, "ERROR": 0.75, "WARNING": 0.4, "INFO": 0.1, "DEBUG": 0.0}

# Dominant HTTP status class. Server failures (5xx) matter most; throttling (429) and other client
# errors (4xx) less; 2xx means the request succeeded but was degraded (e.g. slow).
ERROR_CLASS_SCORES: dict[str, float] = {"5xx": 1.0, "429": 0.5, "4xx": 0.4, "2xx": 0.2, "unknown": 0.5}

# (minimum value, factor) pairs, checked from the top; the first match wins.
VOLUME_BANDS: list[tuple[int, float]] = [(50, 1.0), (25, 0.75), (10, 0.5), (5, 0.3), (0, 0.1)]
HOST_BANDS: list[tuple[int, float]] = [(4, 1.0), (3, 0.8), (2, 0.5), (0, 0.25)]
PEAK_PER_MINUTE_BANDS: list[tuple[int, float]] = [(6, 1.0), (4, 0.7), (2, 0.4), (0, 0.1)]

# Minimum score for each priority, checked from the top.
def _threshold(name: str, default: float) -> float:
    try:
        return float(os.getenv(f"PRIORITY_{name}_MIN", default))
    except ValueError:
        return default


# Overridable per deployment with PRIORITY_P1_MIN / PRIORITY_P2_MIN / PRIORITY_P3_MIN (P4 is the floor).
PRIORITY_THRESHOLDS: list[tuple[str, float]] = [
    ("P1", _threshold("P1", 80.0)), ("P2", _threshold("P2", 65.0)), ("P3", _threshold("P3", 50.0)), ("P4", 0.0),
]
