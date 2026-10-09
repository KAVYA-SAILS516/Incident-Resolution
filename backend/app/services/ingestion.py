"""Ingestion pipeline: raw log files -> parse -> detect -> classify workflow -> prioritise -> persist.

Raw logs are read-only. Every ingest recomputes the full picture from all raw files in data/logs/
(and data/logs/uploads/), so the result is deterministic and repeatable.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import settings
from app.schemas.incident import Incident
from app.schemas.log import IngestSummary, LogEntry, ParseFailure
from app.application.models import ApplicationKnowledge
from app.services.correlation import apply_application_context, correlate
from app.services.incident_detector import detect_incidents
from app.services.log_parser import parse_file
from app.services.priority_engine import prioritize
from app.services.workflow_classifier import classify_workflow
from app.state.store import JsonStore
from app.telemetry.otel import SOURCE as OTEL_SOURCE, fetch_trace, load_events

LOG_SUFFIXES = {".log", ".txt"}
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def raw_log_files(logs_dir: Path) -> list[Path]:
    files = [p for p in logs_dir.glob("*") if p.is_file() and p.suffix.lower() in LOG_SUFFIXES]
    uploads = logs_dir / "uploads"
    if uploads.is_dir():
        files += [p for p in uploads.glob("*") if p.is_file() and p.suffix.lower() in LOG_SUFFIXES]
    return sorted(files)


def save_upload(logs_dir: Path, filename: str, content: bytes) -> Path:
    """Store an uploaded file as a new raw source. Never overwrites an existing file."""
    uploads = logs_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    stem = _UNSAFE.sub("_", Path(filename or "upload.log").stem)[:80] or "upload"
    suffix = Path(filename or "").suffix.lower() if Path(filename or "").suffix.lower() in LOG_SUFFIXES else ".log"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    target = uploads / f"{stamp}_{stem}{suffix}"
    with target.open("xb") as handle:  # "x": fail rather than overwrite
        handle.write(content)
    return target


def enrich(incident: Incident) -> Incident:
    """Classify the workflow and score the priority (application context and correlation are already set)."""
    classification = classify_workflow(incident.affected_endpoints, incident.service)
    incident.workflow = classification.workflow
    incident.workflow_criticality = classification.criticality
    incident.workflow_reason = classification.reason
    return prioritize(incident)


def ingest(store: JsonStore, logs_dir: Path | None = None) -> IngestSummary:
    logs_dir = logs_dir or store.data_dir / "logs"
    # Runtime telemetry comes only from the application's OpenTelemetry stack; log files are read only when the
    # legacy file import is explicitly enabled (tests, fixtures).
    files = raw_log_files(logs_dir) if settings.file_log_ingest else []
    entries: list[LogEntry] = []
    failures: list[ParseFailure] = []
    total = 0
    sources = []
    for path in files:
        source = path.relative_to(logs_dir).as_posix()
        result = parse_file(path, source)
        sources.append(source)
        total += result.total_lines
        entries.extend(result.entries)
        failures.extend(result.failures)
    telemetry = load_events(store.data_dir)  # OpenTelemetry events pulled earlier (already LogEntry objects)
    if telemetry:
        sources.append(OTEL_SOURCE)
        total += len(telemetry)
        entries.extend(telemetry)
    ignored: list[str] = []
    if store.application is not None:  # only the monitored application: other services' telemetry (e.g. the Collector's own) is not an incident source
        known = {s.name for s in store.application.services}
        ignored = sorted({e.service for e in entries if e.service not in known})
        entries = [e for e in entries if e.service in known]
    entries.sort(key=lambda e: e.timestamp)

    detection = detect_incidents(
        entries,
        window_minutes=settings.detection_window_minutes,
        min_occurrences=settings.detection_min_occurrences,
        family_depth=settings.endpoint_family_depth,
    )
    knowledge: ApplicationKnowledge | None = store.application
    by_id = {e.log_id: e for e in entries}
    for incident in detection.incidents:
        apply_application_context(incident, knowledge, [by_id[i] for i in incident.log_ids if i in by_id])
    correlate(detection.incidents, by_id, entries, knowledge,
              trace_lookup=fetch_trace if settings.otel_enabled and settings.otel_traces_url else None)
    incidents = sorted((enrich(i) for i in detection.incidents), key=lambda i: (-i.priority_score, i.first_seen))

    summary = IngestSummary(
        sources=sources,
        total_lines=total,
        parsed_lines=len(entries),
        failed_lines=len(failures),
        error_logs=sum(1 for e in entries if e.level in ("ERROR", "CRITICAL")),
        warning_logs=sum(1 for e in entries if e.level == "WARNING"),
        services=sorted({e.service for e in entries}),
        environments=sorted({e.environment for e in entries if e.environment}),
        time_range={
            "start": entries[0].timestamp if entries else None,
            "end": entries[-1].timestamp if entries else None,
        },
        incidents_detected=len(incidents),
        below_threshold_groups=detection.below_threshold_groups,
        failures_sample=failures[:20],
        ignored_services=ignored,
        ingested_at=datetime.now(timezone.utc),
    )
    store.replace_ingest(entries, incidents, summary)
    return summary
