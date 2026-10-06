"""Parse `<ISO timestamp> key=value key="quoted value" ...` application log lines into LogEntry.

Malformed lines never stop parsing: each one is reported as a ParseFailure with a reason.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.log import LogEntry, ParseFailure, ParseResult

_LINE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(?P<fields>.+)$")
_FIELD = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')

_LEVEL_ALIASES = {"WARN": "WARNING", "ERR": "ERROR", "FATAL": "CRITICAL", "CRIT": "CRITICAL"}
_EMPTY = {"", "NONE", "NULL", "N/A", "-"}
_REQUIRED = ("level", "service")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return None if value.upper() in _EMPTY else value


def _parse_timestamp(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # assume UTC when the log omits an offset
    return parsed.astimezone(timezone.utc)


def _number(value: str | None, cast):
    value = _clean(value)
    if value is None:
        return None
    return cast(value)


def parse_line(line: str, source: str, line_number: int) -> LogEntry | ParseFailure:
    raw = line.rstrip("\r\n")

    def fail(reason: str) -> ParseFailure:
        return ParseFailure(source=source, line_number=line_number, reason=reason, raw_line=raw[:300])

    match = _LINE.match(raw.strip())
    if not match:
        return fail("no leading ISO-8601 timestamp")
    try:
        timestamp = _parse_timestamp(match["ts"])
    except ValueError:
        return fail(f"invalid timestamp {match['ts']!r}")

    fields: dict[str, str] = {}
    for key, value in _FIELD.findall(match["fields"]):
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1].replace('\\"', '"')
        fields[key.lower()] = value
    missing = [name for name in _REQUIRED if not _clean(fields.get(name))]
    if missing:
        return fail(f"missing required field(s): {', '.join(missing)}")

    level = fields["level"].strip().upper()
    try:
        status_code = _number(fields.get("status_code"), int)
        response_time_ms = _number(fields.get("response_time_ms"), float)
    except ValueError as exc:
        return fail(f"non-numeric value: {exc}")

    return LogEntry(
        log_id=f"{source}:{line_number}",
        source=source,
        line_number=line_number,
        timestamp=timestamp,
        level=_LEVEL_ALIASES.get(level, level),
        service=fields["service"].strip(),
        environment=_clean(fields.get("environment")),
        host=_clean(fields.get("host")),
        request_id=_clean(fields.get("request_id")),
        client_id=_clean(fields.get("client_id")),
        method=(_clean(fields.get("method")) or "").upper() or None,
        endpoint=_clean(fields.get("endpoint")),
        status_code=status_code,
        response_time_ms=response_time_ms,
        error_type=(_clean(fields.get("error_type")) or "").upper() or None,
        message=_clean(fields.get("message")),
        raw_message=raw,
    )


def parse_lines(lines, source: str) -> ParseResult:
    result = ParseResult(source=source)
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue  # blank lines are not log records
        result.total_lines += 1
        parsed = parse_line(line, source, number)
        if isinstance(parsed, LogEntry):
            result.entries.append(parsed)
        else:
            result.failures.append(parsed)
    return result


def parse_file(path: Path, source: str | None = None) -> ParseResult:
    """Read-only: the source file is opened for reading and never modified."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return parse_lines(handle, source or path.name)
