"""Runtime settings, read once from environment variables (and backend/.env if present).

Only the Vertex AI settings are needed to run the agents; everything else has a sensible default.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent

# ENV_FILE lets tests (or another deployment) point at a different file; nothing else reads it.
load_dotenv(os.getenv("ENV_FILE") or BACKEND_DIR / ".env")


_PLACEHOLDER = re.compile(r"^<.*>$|^your-[a-z-]+$", re.IGNORECASE)


def _str(*names: str) -> str | None:
    """First non-empty value among the given environment variable names. A template placeholder such as
    `<MY_PROJECT_ID>` or `your-project-id` counts as NOT configured, never as a real value."""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value and not _PLACEHOLDER.match(value):
            return value
    return None


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
    gcp_project: str | None = field(default_factory=lambda: _str("GOOGLE_CLOUD_PROJECT"))
    gcp_location: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    model: str = field(default_factory=lambda: _str("GEMINI_MODEL", "VERTEX_MODEL") or "gemini-2.5-flash")
    # Which provider serves the agents. "vertex" = Gemini on Vertex AI, the only real provider (no silent
    # fallback). Tests inject a scripted model via agents.runner.set_model_override, reported as provider "fake".
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "vertex").strip().lower())
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

    # The one monitored application. When APPLICATION_PATH is set it is scanned at startup (and by the Applications
    # page); nothing is monitored until an application has been scanned.
    application_name: str = field(default_factory=lambda: os.getenv("APPLICATION_NAME", "OpenTelemetry Astronomy Shop"))
    application_path: str | None = field(default_factory=lambda: _str("ASTRONOMY_SHOP_PATH", "APPLICATION_PATH"))
    # Legacy plain-log-file import (data/logs/*.log). OFF at runtime: the only runtime telemetry is the monitored
    # application's own OpenTelemetry stack, and there is never a fallback to local log files. Tests switch it on
    # for the parser/detector fixtures.
    file_log_ingest: bool = field(default_factory=lambda: _bool("FILE_LOG_INGEST", False))

    # Application discovery. When APPLICATION_ROOT is set, scanned paths must be inside it.
    application_root: Path | None = field(
        default_factory=lambda: Path(os.environ["APPLICATION_ROOT"]).resolve() if os.getenv("APPLICATION_ROOT") else None
    )

    # OpenTelemetry runtime. The application's Collector exports to a telemetry backend; we read from it.
    # OTEL_ENDPOINT is the Collector's OTLP endpoint (for reference / status); the *_URL settings are the
    # query APIs of the backends the Collector exports to (OpenSearch for logs, Jaeger for traces,
    # Prometheus for metrics). Any of them may be left unset: that signal is then reported as unavailable.
    otel_enabled: bool = field(default_factory=lambda: _bool("OTEL_ENABLED", False))
    otel_endpoint: str | None = field(default_factory=lambda: _str("OTEL_ENDPOINT"))
    otel_logs_url: str | None = field(default_factory=lambda: _str("OTEL_LOGS_URL"))
    otel_logs_index: str = field(default_factory=lambda: os.getenv("OTEL_LOGS_INDEX", "otel-logs*"))
    otel_traces_url: str | None = field(default_factory=lambda: _str("OTEL_TRACES_URL"))
    otel_metrics_url: str | None = field(default_factory=lambda: _str("OTEL_METRICS_URL"))
    otel_lookback_minutes: int = field(default_factory=lambda: _int("OTEL_LOOKBACK_MINUTES", 30))
    otel_timeout_seconds: int = field(default_factory=lambda: _int("OTEL_TIMEOUT_SECONDS", 10))

    # Automatic telemetry reading: how often the backend reads the application's telemetry and runs detection by itself
    # (seconds; 0 turns it off, then only the Read Telemetry button / POST /api/telemetry/pull reads it).
    telemetry_poll_seconds: int = field(default_factory=lambda: _int("TELEMETRY_POLL_SECONDS", 60))

    # Metric-based detection (Prometheus span metrics): failures that are not logged as WARN/ERROR still show up as a
    # high error ratio or a latency jump. An anomalous step needs at least `metric_min_requests` spans in the window.
    metric_detection: bool = field(default_factory=lambda: _bool("METRIC_DETECTION", True))
    metric_window_seconds: int = field(default_factory=lambda: _int("METRIC_WINDOW_SECONDS", 120))
    metric_step_seconds: int = field(default_factory=lambda: max(5, _int("METRIC_STEP_SECONDS", 30)))
    metric_min_requests: int = field(default_factory=lambda: _int("METRIC_MIN_REQUESTS", 3))
    metric_error_ratio_min: float = field(default_factory=lambda: float(os.getenv("METRIC_ERROR_RATIO_MIN", "0.25")))
    metric_latency_min_ms: float = field(default_factory=lambda: float(os.getenv("METRIC_LATENCY_MIN_MS", "1000")))
    metric_latency_factor: float = field(default_factory=lambda: float(os.getenv("METRIC_LATENCY_FACTOR", "3")))

    # Resolution: remediation actions are guarded and simulated by default (nothing touches infrastructure).
    remediation_max_attempts: int = field(default_factory=lambda: max(1, _int("REMEDIATION_MAX_ATTEMPTS", 2)))
    verification_wait_seconds: int = field(default_factory=lambda: _int("VERIFICATION_WAIT_SECONDS", 60))

    cors_origins: list[str] = field(
        default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    )

    @property
    def llm_configured(self) -> bool:
        return bool(self.gcp_project)

    @property
    def configuration_issues(self) -> list[str]:
        """Required settings that are missing, as messages for the UI / health check (never fake values)."""
        issues = []
        if self.ai_provider != "vertex":
            issues.append(f"AI_PROVIDER={self.ai_provider!r} is not supported; use 'vertex'.")
        if not self.gcp_project:
            issues.append("GOOGLE_CLOUD_PROJECT is not configured. Vertex AI is not configured: set the Google Cloud "
                          "project and authenticate with Application Default Credentials.")
        if not self.application_path:
            issues.append("ASTRONOMY_SHOP_PATH is not configured.")
        if not self.otel_enabled:
            issues.append("OTEL_ENABLED is false: no telemetry is read from the application.")
        else:
            for label, name, value in (("OpenTelemetry logs (OpenSearch)", "OTEL_LOGS_URL", self.otel_logs_url),
                                       ("Prometheus", "OTEL_METRICS_URL", self.otel_metrics_url),
                                       ("Jaeger", "OTEL_TRACES_URL", self.otel_traces_url)):
                if not value:
                    issues.append(f"{label} endpoint is not configured ({name}).")
        return issues

    @property
    def logs_dir(self) -> Path:
        """Raw log files (read-only source data) and uploads."""
        return self.data_dir / "logs"

    @property
    def telemetry_dir(self) -> Path:
        """Normalised OpenTelemetry events pulled from the telemetry backend."""
        return self.data_dir / "telemetry"

    @property
    def runbooks_dir(self) -> Path:
        return self.data_dir / "runbooks"


settings = Settings()
