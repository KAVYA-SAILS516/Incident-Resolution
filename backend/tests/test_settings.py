"""Environment configuration: names, aliases, placeholders and fail-fast messages."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config.settings import Settings

BACKEND = Path(__file__).resolve().parents[1]


def test_aliases_and_exact_values(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west4")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("ASTRONOMY_SHOP_PATH", r"D:\apps\shop")
    s = Settings()
    assert (s.gcp_project, s.gcp_location, s.model, s.application_path) == ("my-project", "europe-west4", "gemini-test", r"D:\apps\shop")
    monkeypatch.delenv("GEMINI_MODEL")
    monkeypatch.delenv("ASTRONOMY_SHOP_PATH")
    monkeypatch.setenv("VERTEX_MODEL", "legacy-model")
    monkeypatch.setenv("APPLICATION_PATH", r"D:\legacy")
    s = Settings()
    assert (s.model, s.application_path) == ("legacy-model", r"D:\legacy")  # the original names still work


def test_placeholders_count_as_not_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "<MY_PROJECT_ID>")
    monkeypatch.setenv("ASTRONOMY_SHOP_PATH", "<MY_ASTRONOMY_SHOP_PATH>")
    monkeypatch.setenv("OTEL_LOGS_URL", "your-logs-url")
    s = Settings()
    assert s.gcp_project is None and s.llm_configured is False and s.application_path is None and s.otel_logs_url is None


def test_missing_required_configuration_is_reported_not_faked(monkeypatch):
    for name in ("GOOGLE_CLOUD_PROJECT", "ASTRONOMY_SHOP_PATH", "APPLICATION_PATH", "OTEL_ENABLED", "OTEL_LOGS_URL",
                 "OTEL_METRICS_URL", "OTEL_TRACES_URL"):
        monkeypatch.delenv(name, raising=False)
    issues = " | ".join(Settings().configuration_issues)
    assert "GOOGLE_CLOUD_PROJECT is not configured" in issues and "Vertex AI is not configured" in issues
    assert "ASTRONOMY_SHOP_PATH is not configured" in issues and "OTEL_ENABLED is false" in issues
    monkeypatch.setenv("OTEL_ENABLED", "true")
    issues = " | ".join(Settings().configuration_issues)
    assert "Prometheus endpoint is not configured" in issues and "Jaeger endpoint is not configured" in issues
    assert "OpenTelemetry logs (OpenSearch) endpoint is not configured" in issues
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("ASTRONOMY_SHOP_PATH", "x")
    monkeypatch.setenv("OTEL_LOGS_URL", "http://a")
    monkeypatch.setenv("OTEL_METRICS_URL", "http://b")
    monkeypatch.setenv("OTEL_TRACES_URL", "http://c")
    assert Settings().configuration_issues == []


def test_env_file_is_loaded_and_never_names_the_sample_application(tmp_path):
    env = tmp_path / "test.env"
    env.write_text("GOOGLE_CLOUD_PROJECT=from-file\nGEMINI_MODEL=file-model\nASTRONOMY_SHOP_PATH=C:\incident-data\astronomy-shop\n", encoding="utf-8")
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("GOOGLE_", "GEMINI_", "ASTRONOMY_", "VERTEX_", "APPLICATION_"))}
    code = "from app.config.settings import settings as s; print(s.gcp_project, s.model, s.application_path)"
    out = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); %s" % (str(BACKEND), code)],
                         env={**clean, "ENV_FILE": str(env)}, capture_output=True, text=True, check=True).stdout.split()
    assert out == ["from-file", "file-model", "C:\incident-data\astronomy-shop"]
    for real in (BACKEND / ".env", BACKEND / ".env.example"):
        if real.exists():
            assert "sample" not in real.read_text(encoding="utf-8").lower()


def test_real_env_is_git_ignored_and_the_example_is_tracked():
    root = BACKEND.parent
    ignored = subprocess.run(["git", "check-ignore", "backend/.env"], cwd=root, capture_output=True, text=True)
    assert ignored.returncode == 0
    tracked = subprocess.run(["git", "ls-files", "backend/.env", "backend/.env.example"], cwd=root, capture_output=True, text=True).stdout.split()
    assert tracked == ["backend/.env.example"]
