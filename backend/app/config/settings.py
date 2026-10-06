"""Runtime settings, read once from environment variables (and backend/.env if present).

Only the Vertex AI settings are needed to run the agents; everything else has a sensible default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent

load_dotenv(BACKEND_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", PROJECT_DIR / "data")).resolve())

    # Vertex AI (Application Default Credentials; no API key).
    gcp_project: str | None = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT") or None)
    gcp_location: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    model: str = field(default_factory=lambda: os.getenv("VERTEX_MODEL", "gemini-2.5-flash"))
    llm_timeout_seconds: int = field(default_factory=lambda: _int("LLM_TIMEOUT_SECONDS", 120))
    llm_max_attempts: int = field(default_factory=lambda: _int("LLM_MAX_ATTEMPTS", 4))

    # Both agents run as a background workflow, independent of the UI: every incident is analysed,
    # highest priority first, as soon as it exists (after ingest, or at startup for incidents left over
    # from a previous run). Opening an incident in the UI never starts an agent.
    auto_analyze: bool = field(default_factory=lambda: _bool("AUTO_ANALYZE", True))
    max_concurrent_analyses: int = field(default_factory=lambda: max(1, _int("MAX_CONCURRENT_ANALYSES", 4)))

    # Incident detection (deterministic grouping).
    detection_window_minutes: int = field(default_factory=lambda: _int("DETECTION_WINDOW_MINUTES", 10))
    detection_min_occurrences: int = field(default_factory=lambda: _int("DETECTION_MIN_OCCURRENCES", 5))
    endpoint_family_depth: int = field(default_factory=lambda: _int("ENDPOINT_FAMILY_DEPTH", 1))

    cors_origins: list[str] = field(
        default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    )

    @property
    def llm_configured(self) -> bool:
        return bool(self.gcp_project)

    @property
    def logs_dir(self) -> Path:
        """Raw log files (read-only source data) and uploads."""
        return self.data_dir / "logs"

    @property
    def runbooks_dir(self) -> Path:
        return self.data_dir / "runbooks"


settings = Settings()
