"""Deterministic priority scoring (P1-P4). Weights, bands and thresholds live in config/priority_rules.py."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import priority_rules as rules
from app.schemas.incident import Incident, PriorityFactor


@dataclass(frozen=True)
class PriorityInputs:
    workflow: str
    criticality: str
    severity: str
    status_code: int | None
    occurrences: int
    host_count: int
    peak_per_minute: int


@dataclass(frozen=True)
class PriorityResult:
    priority: str
    score: float
    reasons: list[str]
    factors: list[PriorityFactor]


def _band(value: int, bands: list[tuple[int, float]]) -> float:
    for minimum, factor in bands:
        if value >= minimum:
            return factor
    return bands[-1][1]


def error_class(status_code: int | None) -> str:
    if status_code is None:
        return "unknown"
    if status_code >= 500:
        return "5xx"
    if status_code == 429:
        return "429"
    if status_code >= 400:
        return "4xx"
    return "2xx"


def score_priority(inputs: PriorityInputs) -> PriorityResult:
    criticality_points = rules.CRITICALITY_SCORES.get(inputs.criticality, rules.CRITICALITY_SCORES["UNKNOWN"])
    status_class = error_class(inputs.status_code)
    observed = {
        "workflow_criticality": (
            f"{inputs.workflow} = {inputs.criticality} ({criticality_points}/5)",
            criticality_points / max(rules.CRITICALITY_SCORES.values()),
        ),
        "severity": (f"highest level {inputs.severity}", rules.SEVERITY_SCORES.get(inputs.severity, 0.0)),
        "error_class": (
            f"HTTP {inputs.status_code if inputs.status_code is not None else 'n/a'} ({status_class})",
            rules.ERROR_CLASS_SCORES[status_class],
        ),
        "volume": (f"{inputs.occurrences} occurrences", _band(inputs.occurrences, rules.VOLUME_BANDS)),
        "blast_radius": (f"{inputs.host_count} host(s) affected", _band(inputs.host_count, rules.HOST_BANDS)),
        "burst_rate": (f"peak {inputs.peak_per_minute}/min", _band(inputs.peak_per_minute, rules.PEAK_PER_MINUTE_BANDS)),
    }

    factors: list[PriorityFactor] = []
    for name, weight in rules.WEIGHTS.items():
        value, normalized = observed[name]
        factors.append(
            PriorityFactor(name=name, value=value, normalized=round(normalized, 3), weight=weight,
                           points=round(100 * weight * normalized, 1))
        )
    score = round(sum(f.points for f in factors), 1)
    priority = next(p for p, minimum in rules.PRIORITY_THRESHOLDS if score >= minimum)
    reasons = [f"{f.name.replace('_', ' ')}: {f.value} -> +{f.points:g}" for f in sorted(factors, key=lambda f: -f.points)]
    reasons.append(f"score {score:g}/100 -> {priority}")
    return PriorityResult(priority=priority, score=score, reasons=reasons, factors=factors)


def prioritize(incident: Incident) -> Incident:
    result = score_priority(
        PriorityInputs(
            workflow=incident.workflow,
            criticality=incident.workflow_criticality,
            severity=incident.severity,
            status_code=incident.status_code,
            occurrences=incident.occurrences,
            host_count=len(incident.hosts),
            peak_per_minute=incident.peak_per_minute,
        )
    )
    incident.priority = result.priority
    incident.priority_score = result.score
    incident.priority_reasons = result.reasons
    incident.priority_factors = result.factors
    return incident
