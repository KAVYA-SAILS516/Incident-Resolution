from datetime import datetime, timezone

from app.schemas.log import LogEntry, ParseFailure
from app.services.log_parser import parse_file, parse_line, parse_lines
from tests.conftest import REAL_LOG, log_line


def test_parses_all_fields():
    entry = parse_line(log_line("2026-09-01T09:30:02Z"), "app.log", 7)
    assert isinstance(entry, LogEntry)
    assert entry.log_id == "app.log:7"
    assert entry.timestamp == datetime(2026, 9, 1, 9, 30, 2, tzinfo=timezone.utc)
    assert (entry.level, entry.service, entry.environment, entry.host) == ("ERROR", "payment-service", "production-simulated", "app-01")
    assert (entry.method, entry.endpoint, entry.status_code, entry.response_time_ms) == ("POST", "/api/v1/payments", 503, 1200.0)
    assert entry.error_type == "DATABASE_TIMEOUT"
    assert entry.message == "Database connection timed out"
    assert entry.client_id == "client-1" and entry.request_id
    assert entry.raw_message.startswith("2026-09-01T09:30:02Z")


def test_error_type_none_and_level_aliases():
    entry = parse_line(log_line("2026-09-01T09:00:00Z", level="warn", error_type="NONE", status=200), "a", 1)
    assert entry.level == "WARNING"
    assert entry.error_type is None


def test_timestamp_offset_is_converted_to_utc():
    entry = parse_line(log_line("2026-09-01T11:00:00+02:00"), "a", 1)
    assert entry.timestamp == datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def test_malformed_lines_are_reported_not_raised():
    lines = [
        log_line("2026-09-01T09:00:00Z"),
        "this is not a log line",
        "2026-09-01T09:00:02Z level=ERROR endpoint=/x",  # no service
        "2026-13-45T99:00:00Z level=ERROR service=a",  # impossible date
        log_line("2026-09-01T09:00:04Z").replace("status_code=503", "status_code=abc"),
        "",
    ]
    result = parse_lines(lines, "mixed.log")
    assert result.total_lines == 5  # blank lines are not records
    assert len(result.entries) == 1
    assert len(result.failures) == 4
    assert all(isinstance(f, ParseFailure) and f.reason for f in result.failures)
    assert {f.line_number for f in result.failures} == {2, 3, 4, 5}


def test_real_log_parses_completely_and_stays_unchanged():
    before = REAL_LOG.read_bytes()
    result = parse_file(REAL_LOG)
    assert result.total_lines == 5000
    assert len(result.entries) == 5000 and not result.failures
    assert {e.service for e in result.entries} == {"auth-service", "order-service", "payment-service", "catalog-service"}
    assert REAL_LOG.read_bytes() == before
