import pytest

from app.config.workflows import WORKFLOW_CRITICALITY, WORKFLOW_KEYWORDS
from app.services.workflow_classifier import classify_workflow


@pytest.mark.parametrize(
    ("endpoints", "service", "workflow", "criticality"),
    [
        (["/api/v1/login"], "auth-service", "Authentication", "HIGH"),
        (["/api/v1/token"], "auth-service", "Authentication", "HIGH"),
        (["/api/v1/payments"], "payment-service", "Payment", "CRITICAL"),
        (["/api/v1/refunds"], "payment-service", "Payment", "CRITICAL"),
        (["/api/v1/orders", "/api/v1/orders/status"], "order-service", "Checkout/Order", "CRITICAL"),
        (["/api/v1/search"], "catalog-service", "Search", "MEDIUM"),
        (["/api/v1/products"], "catalog-service", "Data Retrieval", "MEDIUM"),
        (["/api/v1/reports/daily"], "analytics-service", "Reporting", "LOW"),
        (["/healthz"], "gateway", "Monitoring/Health", "HIGH"),
        (["/api/v1/backups/restore"], "ops", "Backup/Recovery", "CRITICAL"),
        (["/api/v1/roles"], "iam", "Authorization", "HIGH"),
    ],
)
def test_classifies_known_workflows(endpoints, service, workflow, criticality):
    result = classify_workflow(endpoints, service)
    assert (result.workflow, result.criticality) == (workflow, criticality)
    assert result.reason


def test_endpoint_outweighs_service_name():
    assert classify_workflow(["/api/v1/search"], "order-service").workflow == "Search"


def test_unknown_when_nothing_matches():
    result = classify_workflow(["/api/v1/zzz"], "mystery")
    assert (result.workflow, result.criticality) == ("Unknown", "UNKNOWN")


def test_orders_status_is_not_monitoring():
    assert classify_workflow(["/api/v1/orders/status"], None).workflow == "Checkout/Order"


def test_catalogue_is_complete():
    assert len(WORKFLOW_KEYWORDS) == 20
    assert set(WORKFLOW_KEYWORDS) | {"Unknown"} == set(WORKFLOW_CRITICALITY)
