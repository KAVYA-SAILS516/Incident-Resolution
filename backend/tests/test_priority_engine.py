from dataclasses import replace

import pytest

from app.config import priority_rules
from app.services.priority_engine import PriorityInputs, error_class, score_priority

BASE = PriorityInputs(workflow="Payment", criticality="CRITICAL", severity="ERROR", status_code=503,
                      occurrences=12, host_count=3, peak_per_minute=3)


def test_payment_outranks_reporting_with_same_signals():
    payment = score_priority(BASE)
    reporting = score_priority(replace(BASE, workflow="Reporting", criticality="LOW"))
    assert payment.score > reporting.score
    assert payment.priority != reporting.priority
    assert int(payment.priority[1]) < int(reporting.priority[1])


def test_each_factor_moves_the_score_monotonically():
    base = score_priority(BASE).score
    assert score_priority(replace(BASE, severity="CRITICAL")).score > base
    assert score_priority(replace(BASE, occurrences=80)).score > base
    assert score_priority(replace(BASE, host_count=4)).score > base
    assert score_priority(replace(BASE, peak_per_minute=10)).score > base
    assert score_priority(replace(BASE, status_code=401)).score < base
    assert score_priority(replace(BASE, criticality="UNKNOWN")).score < base


def test_score_is_bounded_and_explained():
    top = score_priority(PriorityInputs("Payment", "CRITICAL", "CRITICAL", 500, 500, 10, 50))
    low = score_priority(PriorityInputs("Unknown", "UNKNOWN", "WARNING", 200, 1, 1, 1))
    assert top.score == pytest.approx(100.0) and top.priority == "P1"
    assert 0 <= low.score < 50 and low.priority == "P4"
    assert len(top.factors) == len(priority_rules.WEIGHTS)
    assert top.reasons[-1].endswith("P1")
    assert sum(f.points for f in top.factors) == pytest.approx(top.score, abs=0.2)


def test_thresholds_and_weights_are_configurable(monkeypatch):
    monkeypatch.setattr(priority_rules, "PRIORITY_THRESHOLDS", [("P1", 10.0), ("P4", 0.0)])
    assert score_priority(replace(BASE, criticality="LOW")).priority == "P1"


def test_weights_sum_to_one():
    assert sum(priority_rules.WEIGHTS.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(("code", "expected"), [(503, "5xx"), (429, "429"), (401, "4xx"), (200, "2xx"), (None, "unknown")])
def test_error_class(code, expected):
    assert error_class(code) == expected
