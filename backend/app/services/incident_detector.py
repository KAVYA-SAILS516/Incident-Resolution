"""Deterministic incident detection and grouping.

Abnormal log entries are grouped by (service, error type, environment, endpoint family). Within a group,
entries are split into episodes: an entry joins the current episode when it arrives within
`window_minutes` of that episode's previous entry. Episodes with at least `min_occurrences` entries
become incidents; smaller ones are counted as below-threshold background noise.

The incident id is a hash of the grouping key plus first_seen, so re-ingesting the same logs yields
the same ids (and agent results stay attached to them).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from app.schemas.incident import Incident
from app.schemas.log import LogEntry

_LEVEL_RANK = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
_ID_SEGMENT = re.compile(r"^(\d+|[0-9a-f]{8,}|[0-9a-f-]{32,36}|[A-Z]{2,}-\d+)$", re.IGNORECASE)
_VERSION_SEGMENT = re.compile(r"^v\d+$", re.IGNORECASE)


def normalize_endpoint(endpoint: str | None) -> str:
    """Lower-case, drop the query string and replace id-like segments with {id}."""
    if not endpoint:
        return "(none)"
    path = endpoint.split("?", 1)[0].strip().lower().rstrip("/") or "/"
    parts = ["{id}" if _ID_SEGMENT.match(p) else p for p in path.split("/")]
    return "/".join(parts) or "/"


def endpoint_family(endpoint: str | None, depth: int = 1) -> str:
    """The resource an endpoint belongs to: /api/v1/orders/status -> /orders (depth 1)."""
    normalized = normalize_endpoint(endpoint)
    if normalized == "(none)":
        return normalized
    segments = [s for s in normalized.split("/") if s and s != "api" and not _VERSION_SEGMENT.match(s)]
    segments = [s for s in segments if s != "{id}"][: max(depth, 1)]
    return "/" + "/".join(segments) if segments else "/"


def error_signature(entry: LogEntry) -> str:
    if entry.error_type:
        return entry.error_type
    if entry.status_code:
        return f"HTTP_{entry.status_code}"
    return f"{entry.level}_LOG"


@dataclass
class Episode:
    service: str
    error_type: str
    environment: str | None
    family: str
    entries: list[LogEntry] = field(default_factory=list)


@dataclass
class DetectionResult:
    incidents: list[Incident]
    below_threshold_groups: int
    abnormal_logs: int


def group_episodes(entries: list[LogEntry], window_minutes: int, family_depth: int) -> list[Episode]:
    window = timedelta(minutes=window_minutes)
    open_episodes: dict[tuple, Episode] = {}
    episodes: list[Episode] = []
    for entry in sorted((e for e in entries if e.is_abnormal), key=lambda e: e.timestamp):
        key = (entry.service, error_signature(entry), entry.environment, endpoint_family(entry.endpoint, family_depth))
        current = open_episodes.get(key)
        if current is None or entry.timestamp - current.entries[-1].timestamp > window:
            current = Episode(*key)
            open_episodes[key] = current
            episodes.append(current)
        current.entries.append(entry)
    return episodes


def _incident_id(episode: Episode) -> str:
    first = episode.entries[0].timestamp.isoformat()
    raw = "|".join([episode.service, episode.error_type, episode.environment or "", episode.family, first])
    return "INC-" + hashlib.sha1(raw.encode()).hexdigest()[:8].upper()


def build_incident(episode: Episode) -> Incident:
    entries = episode.entries
    first, last = entries[0].timestamp, entries[-1].timestamp
    levels = Counter(e.level for e in entries)
    statuses = Counter(str(e.status_code) for e in entries if e.status_code is not None)
    per_minute = Counter(e.timestamp.replace(second=0, microsecond=0) for e in entries)
    severity = max(levels, key=lambda lvl: _LEVEL_RANK.get(lvl, 0))
    endpoints = sorted({e.endpoint for e in entries if e.endpoint})
    return Incident(
        incident_id=_incident_id(episode),
        title=f"{episode.error_type} on {episode.service} {episode.family}",
        service=episode.service,
        environment=episode.environment,
        error_type=episode.error_type,
        status_code=int(statuses.most_common(1)[0][0]) if statuses else None,
        endpoint_family=episode.family,
        affected_endpoints=endpoints,
        occurrences=len(entries),
        first_seen=first,
        last_seen=last,
        duration_seconds=int((last - first).total_seconds()),
        severity=severity,
        levels=dict(levels),
        status_codes=dict(statuses),
        hosts=sorted({e.host for e in entries if e.host}),
        distinct_clients=len({e.client_id for e in entries if e.client_id}),
        peak_per_minute=max(per_minute.values()),
        log_ids=[e.log_id for e in entries],
    )


def detect_incidents(
    entries: list[LogEntry], *, window_minutes: int = 10, min_occurrences: int = 5, family_depth: int = 1
) -> DetectionResult:
    episodes = group_episodes(entries, window_minutes, family_depth)
    incidents = [build_incident(ep) for ep in episodes if len(ep.entries) >= min_occurrences]
    return DetectionResult(
        incidents=incidents,
        below_threshold_groups=sum(1 for ep in episodes if len(ep.entries) < min_occurrences),
        abnormal_logs=sum(len(ep.entries) for ep in episodes),
    )
