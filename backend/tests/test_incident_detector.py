from app.services.incident_detector import detect_incidents, endpoint_family, normalize_endpoint
from app.services.log_parser import parse_file, parse_lines
from tests.conftest import REAL_LOG, log_line


def _entries(lines):
    return parse_lines(lines, "t.log").entries


def _burst(start_minute: int, count: int, **kwargs):
    return [log_line(f"2026-09-01T09:{start_minute + i // 6:02d}:{(i * 10) % 60:02d}Z", **kwargs) for i in range(count)]


def test_endpoint_normalisation_and_family():
    assert normalize_endpoint("/api/v1/orders/12345?x=1") == "/api/v1/orders/{id}"
    assert endpoint_family("/api/v1/orders/status") == "/orders"
    assert endpoint_family("/api/v1/orders/9f8e7d6c5b4a") == "/orders"
    assert endpoint_family("/api/v1/payments") != endpoint_family("/api/v1/refunds")
    assert endpoint_family(None) == "(none)"


def test_groups_same_signature_within_window():
    lines = _burst(0, 6) + _burst(2, 3, endpoint="/api/v1/payments/987654")
    result = detect_incidents(_entries(lines), window_minutes=10, min_occurrences=5)
    assert len(result.incidents) == 1
    incident = result.incidents[0]
    assert incident.occurrences == 9
    assert incident.affected_endpoints == ["/api/v1/payments", "/api/v1/payments/987654"]
    assert incident.status_code == 503 and incident.error_type == "DATABASE_TIMEOUT"
    assert incident.first_seen < incident.last_seen
    assert len(incident.log_ids) == 9


def test_splits_on_service_error_type_environment_and_endpoint():
    lines = (_burst(0, 5) + _burst(0, 5, service="order-service", endpoint="/api/v1/orders")
             + _burst(0, 5, error_type="INTERNAL_SERVER_ERROR", status=500)
             + _burst(0, 5, env="staging") + _burst(0, 5, endpoint="/api/v1/refunds"))
    result = detect_incidents(_entries(lines), window_minutes=10, min_occurrences=5)
    assert len(result.incidents) == 5


def test_time_window_splits_episodes_and_threshold_filters_noise():
    lines = _burst(0, 5) + _burst(40, 5) + _burst(20, 2, service="catalog-service", endpoint="/api/v1/search")
    result = detect_incidents(_entries(lines), window_minutes=10, min_occurrences=5)
    assert len(result.incidents) == 2
    assert result.below_threshold_groups == 1
    # A wider window merges the two episodes.
    assert len(detect_incidents(_entries(lines), window_minutes=60, min_occurrences=5).incidents) == 1


def test_info_lines_are_ignored():
    lines = _burst(0, 10, level="INFO", error_type="NONE", status=200)
    assert detect_incidents(_entries(lines)).incidents == []


def test_ids_are_stable_across_runs():
    entries = parse_file(REAL_LOG).entries
    first = [i.incident_id for i in detect_incidents(entries).incidents]
    second = [i.incident_id for i in detect_incidents(entries).incidents]
    assert first == second and len(set(first)) == len(first)


def test_real_log_produces_incidents_covering_known_error_types():
    result = detect_incidents(parse_file(REAL_LOG).entries, window_minutes=10, min_occurrences=5)
    assert result.incidents
    assert result.abnormal_logs == 513
    assert {"SLOW_REQUEST", "INTERNAL_SERVER_ERROR"} <= {i.error_type for i in result.incidents}
    assert all(i.occurrences >= 5 for i in result.incidents)
