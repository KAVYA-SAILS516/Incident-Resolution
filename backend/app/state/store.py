"""Local JSON state (no database). Everything is held in memory and written through to data/.

Files:
  data/logs/parsed_logs.json          normalised log entries from the last ingest
  data/logs/ingest_summary.json       the last ingest summary
  data/incidents/incidents.json       detected incidents
  data/incidents/investigations.json  Investigation Agent results, keyed by incident_id
  data/incidents/recommendations.json Resolution Decision Agent results, keyed by incident_id
  data/incidents/resolutions.json     Resolution Decision, remediation attempts and verifications, by incident_id
  data/application/knowledge.json     the scanned ApplicationKnowledge
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.application.models import ApplicationKnowledge
from app.config.settings import settings
from app.schemas.incident import Incident
from app.schemas.investigation import InvestigationRecord
from app.schemas.log import IngestSummary, LogEntry
from app.schemas.recommendation import RecommendationRecord
from app.schemas.resolution import ResolutionRecord


REPLACE_ATTEMPTS = 8


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)  # atomic: readers never see a half-written file
            return
        except PermissionError:  # Windows: a sync client / antivirus briefly holds the target open
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.1 * (attempt + 1))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


class JsonStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._lock = threading.RLock()
        self.logs: list[LogEntry] = []
        self.logs_by_id: dict[str, LogEntry] = {}
        self.summary: IngestSummary | None = None
        self.incidents: dict[str, Incident] = {}
        self.investigations: dict[str, InvestigationRecord] = {}
        self.recommendations: dict[str, RecommendationRecord] = {}
        self.resolutions: dict[str, ResolutionRecord] = {}
        self.application: ApplicationKnowledge | None = None
        self.load()

    # --- paths ---
    @property
    def logs_path(self) -> Path:
        return self.data_dir / "logs" / "parsed_logs.json"

    @property
    def summary_path(self) -> Path:
        return self.data_dir / "logs" / "ingest_summary.json"

    @property
    def incidents_path(self) -> Path:
        return self.data_dir / "incidents" / "incidents.json"

    @property
    def investigations_path(self) -> Path:
        return self.data_dir / "incidents" / "investigations.json"

    @property
    def recommendations_path(self) -> Path:
        return self.data_dir / "incidents" / "recommendations.json"

    @property
    def resolutions_path(self) -> Path:
        return self.data_dir / "incidents" / "resolutions.json"

    @property
    def application_path(self) -> Path:
        return self.data_dir / "application" / "knowledge.json"

    # --- load / save ---
    def load(self) -> None:
        with self._lock:
            self.logs = [LogEntry.model_validate(e) for e in _read_json(self.logs_path, [])]
            self.logs_by_id = {e.log_id: e for e in self.logs}
            raw_summary = _read_json(self.summary_path, None)
            self.summary = IngestSummary.model_validate(raw_summary) if raw_summary else None
            self.incidents = {i["incident_id"]: Incident.model_validate(i) for i in _read_json(self.incidents_path, [])}
            self.investigations = {
                k: InvestigationRecord.model_validate(v) for k, v in _read_json(self.investigations_path, {}).items()
            }
            self.recommendations = {
                k: RecommendationRecord.model_validate(v) for k, v in _read_json(self.recommendations_path, {}).items()
            }
            self.resolutions = {
                k: ResolutionRecord.model_validate(v) for k, v in _read_json(self.resolutions_path, {}).items()
            }
            raw_application = _read_json(self.application_path, None)
            self.application = ApplicationKnowledge.model_validate(raw_application) if raw_application else None

    def replace_ingest(self, logs: list[LogEntry], incidents: list[Incident], summary: IngestSummary) -> None:
        """Swap in a fresh ingest. Agent results survive for incidents that still exist (ids are stable)."""
        with self._lock:
            self.logs = logs
            self.logs_by_id = {e.log_id: e for e in logs}
            self.summary = summary
            now = datetime.now(timezone.utc)
            for incident in incidents:
                earlier = self.incidents.get(incident.incident_id)
                incident.detected_at = (earlier.detected_at if earlier and earlier.detected_at else None) or now
            self.incidents = {i.incident_id: i for i in incidents}
            self.investigations = {k: v for k, v in self.investigations.items() if k in self.incidents}
            self.recommendations = {k: v for k, v in self.recommendations.items() if k in self.incidents}
            self.resolutions = {k: v for k, v in self.resolutions.items() if k in self.incidents}
            for incident in self.incidents.values():
                incident.status = self._status_for(incident.incident_id)
            _write_json(self.logs_path, [e.model_dump(mode="json") for e in logs])
            _write_json(self.summary_path, summary.model_dump(mode="json"))
            self._save_incidents()
            self._save_agent_results()

    def save_investigation(self, record: InvestigationRecord) -> None:
        with self._lock:
            self.investigations[record.incident_id] = record
            # A new investigation invalidates recommendations based on the previous one.
            self.recommendations.pop(record.incident_id, None)
            self.incidents[record.incident_id].status = self._status_for(record.incident_id)
            self._save_agent_results()
            self._save_incidents()

    def save_recommendations(self, record: RecommendationRecord) -> None:
        with self._lock:
            self.recommendations[record.incident_id] = record
            self.incidents[record.incident_id].status = self._status_for(record.incident_id)
            self._save_agent_results()
            self._save_incidents()

    def save_resolution(self, record: ResolutionRecord) -> None:
        with self._lock:
            self.resolutions[record.incident_id] = record
            self._save_agent_results()

    def save_application(self, knowledge: ApplicationKnowledge) -> None:
        with self._lock:
            self.application = knowledge
            _write_json(self.application_path, knowledge.model_dump(mode="json"))

    def _status_for(self, incident_id: str) -> str:
        if incident_id in self.recommendations:
            return "recommendations_ready"
        if incident_id in self.investigations:
            return "investigated"
        return "open"

    def _save_incidents(self) -> None:
        _write_json(self.incidents_path, [i.model_dump(mode="json") for i in self.incidents.values()])

    def _save_agent_results(self) -> None:
        _write_json(self.investigations_path, {k: v.model_dump(mode="json") for k, v in self.investigations.items()})
        _write_json(self.recommendations_path, {k: v.model_dump(mode="json") for k, v in self.recommendations.items()})
        _write_json(self.resolutions_path, {k: v.model_dump(mode="json") for k, v in self.resolutions.items()})


_store: JsonStore | None = None


def get_store() -> JsonStore:
    global _store
    if _store is None:
        _store = JsonStore(settings.data_dir)
    return _store


def configure_store(data_dir: Path) -> JsonStore:
    """Point the app at another data directory (used by tests)."""
    global _store
    _store = JsonStore(data_dir)
    return _store
