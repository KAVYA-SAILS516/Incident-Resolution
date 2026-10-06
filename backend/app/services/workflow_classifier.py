"""Deterministic business-workflow classification from endpoints and service name."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.config.workflows import (
    ENDPOINT_MATCH_WEIGHT,
    IGNORED_TOKENS,
    SERVICE_MATCH_WEIGHT,
    UNKNOWN,
    WORKFLOW_CRITICALITY,
    WORKFLOW_KEYWORDS,
)

_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class WorkflowClassification:
    workflow: str
    criticality: str
    reason: str


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    return [t for t in _SPLIT.split(text.lower()) if t and t not in IGNORED_TOKENS and not t.isdigit()]


def _matches(token: str, keyword: str) -> bool:
    return token == keyword or token == keyword + "s" or token == keyword + "es"


def _score_tokens(tokens: list[str], weight: int, scores: Counter, hits: dict[str, set[str]]) -> None:
    for workflow, keywords in WORKFLOW_KEYWORDS.items():
        for token in tokens:
            if any(_matches(token, kw) for kw in keywords):
                scores[workflow] += weight
                hits.setdefault(workflow, set()).add(token)


def classify_workflow(endpoints: list[str], service: str | None = None) -> WorkflowClassification:
    scores: Counter = Counter()
    hits: dict[str, set[str]] = {}
    for endpoint in endpoints:
        _score_tokens(tokenize(endpoint), ENDPOINT_MATCH_WEIGHT, scores, hits)
    _score_tokens(tokenize(service), SERVICE_MATCH_WEIGHT, scores, hits)

    if not scores:
        return WorkflowClassification(UNKNOWN, WORKFLOW_CRITICALITY[UNKNOWN], "No workflow keyword matched the endpoints or service name")

    order = list(WORKFLOW_KEYWORDS)
    workflow = max(scores, key=lambda wf: (scores[wf], -order.index(wf)))
    reason = f"Matched {', '.join(sorted(hits[workflow]))!s} in endpoint/service names (score {scores[workflow]})"
    return WorkflowClassification(workflow, WORKFLOW_CRITICALITY[workflow], reason)
