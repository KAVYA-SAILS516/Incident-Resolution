"""End-to-end API tests on a copy of the real log. Agents run through the real ADK Runner with a scripted
model; AI outputs are checked for schema and properties, not wording."""

import time

import pytest
from fastapi.testclient import TestClient

from app.agents import auto_analysis
from app.main import app
from app.schemas.investigation import InvestigationRecord
from app.schemas.recommendation import RecommendationRecord
from tests.conftest import REAL_LOG, log_line


@pytest.fixture
def client(store):
    with TestClient(app) as test_client:
        yield test_client


def _ingest(client):
    response = client.post("/api/logs/ingest")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_until(predicate, timeout=10.0, step=0.02):
    """Poll a condition (the background workflow runs on the TestClient's own event loop, concurrently
    with the test), rather than assuming any particular timing."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(step)
    raise AssertionError("condition not met within timeout")


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and "llm_configured" in body


def test_ingest_existing_logs(client, store):
    body = _ingest(client)
    assert body["total_lines"] == 5000 and body["parsed_lines"] == 5000 and body["failed_lines"] == 0
    assert body["error_logs"] == 304 and body["warning_logs"] == 209
    assert len(body["services"]) == 4
    assert body["incidents_detected"] > 0
    assert (store.data_dir / "incidents" / "incidents.json").exists()
    assert (store.data_dir / "logs" / "parsed_logs.json").exists()


def test_ingest_upload_adds_a_source_without_touching_existing_files(client, store):
    original = (store.data_dir / "logs" / REAL_LOG.name).read_bytes()
    upload = "\n".join([log_line(f"2026-09-02T08:00:{s:02d}Z") for s in range(0, 60, 5)] + ["garbage line"])
    response = client.post("/api/logs/ingest", files={"file": ("extra.log", upload.encode(), "text/plain")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_lines"] == 5013 and body["failed_lines"] == 1
    assert any(s.startswith("uploads/") for s in body["sources"])
    assert (store.data_dir / "logs" / REAL_LOG.name).read_bytes() == original
    assert client.post("/api/logs/ingest", files={"file": ("x.exe", b"abc", "application/octet-stream")}).status_code == 400


def test_list_filter_and_detail(client):
    _ingest(client)
    listing = client.get("/api/incidents").json()
    assert listing["total"] == len(listing["incidents"]) > 0
    scores = [i["priority_score"] for i in listing["incidents"]]
    assert scores == sorted(scores, reverse=True)
    first = listing["incidents"][0]
    for field in ("incident_id", "status", "service", "environment", "error_type", "status_code", "affected_endpoints",
                  "occurrences", "first_seen", "last_seen", "workflow", "workflow_criticality", "priority",
                  "priority_score", "priority_reasons", "analysis"):
        assert field in first
    assert "log_ids" not in first
    # No background workflow is enabled in this test: nothing has analysed it, so it reports "disabled",
    # not "queued" or "investigating" - the list view never implies work is happening that isn't.
    assert first["analysis"] == {"state": "disabled", "error": "Automatic analysis is turned off (AUTO_ANALYZE=false)"}
    p1 = client.get("/api/incidents", params={"priority": first["priority"]}).json()
    assert all(i["priority"] == first["priority"] for i in p1["incidents"])

    detail = client.get(f"/api/incidents/{first['incident_id']}").json()
    assert detail["incident"]["incident_id"] == first["incident_id"]
    assert detail["evidence"]["representative_logs"]
    assert detail["investigation"] is None and detail["recommendations"] is None
    assert client.get("/api/incidents/INC-NOPE").status_code == 404


def test_investigate_then_recommend(client, fake_llm):
    _ingest(client)
    incident_id = client.get("/api/incidents").json()["incidents"][0]["incident_id"]

    assert client.post(f"/api/incidents/{incident_id}/recommendations").status_code == 409

    response = client.post(f"/api/incidents/{incident_id}/investigate")
    assert response.status_code == 200, response.text
    investigation = InvestigationRecord.model_validate(response.json())
    assert 0.0 <= investigation.confidence <= 1.0
    assert investigation.root_cause and investigation.evidence
    assert {e.kind for e in investigation.evidence} <= {"FACT", "AI_INFERENCE"}
    assert {"get_incident_details", "get_log_statistics", "get_related_errors"} <= set(investigation.tools_called)

    response = client.post(f"/api/incidents/{incident_id}/recommendations")
    assert response.status_code == 200, response.text
    recs = RecommendationRecord.model_validate(response.json())
    assert recs.recommendations
    assert all(r.source in ("runbook", "AI-generated") and r.risk in ("LOW", "MEDIUM", "HIGH") for r in recs.recommendations)
    assert "get_runbook" in recs.tools_called

    detail = client.get(f"/api/incidents/{incident_id}").json()
    assert detail["incident"]["status"] == "recommendations_ready"
    assert detail["incident"]["analysis"] == {"state": "done", "error": None}
    assert detail["investigation"]["root_cause"] and detail["recommendations"]["recommendations"]

    # Re-ingesting keeps ids stable, so agent results stay attached.
    _ingest(client)
    assert client.get(f"/api/incidents/{incident_id}").json()["incident"]["status"] == "recommendations_ready"


def test_opening_an_incident_never_starts_analysis(client):
    """GET requests are read-only: repeatedly viewing an incident must never change its analysis state."""
    _ingest(client)
    incident_id = client.get("/api/incidents").json()["incidents"][0]["incident_id"]
    states = set()
    for _ in range(5):
        states.add(client.get(f"/api/incidents/{incident_id}").json()["incident"]["analysis"]["state"])
    assert states == {"disabled"}  # never "queued" or "investigating" just from being viewed


def test_background_workflow_analyses_every_incident_highest_priority_first(client, background_workflow):
    body = _ingest(client)
    assert body["incidents_detected"] > 0
    incident_ids = [i["incident_id"] for i in client.get("/api/incidents").json()["incidents"]]

    def all_done():
        incidents = client.get("/api/incidents").json()["incidents"]
        return all(i["analysis"]["state"] == "done" for i in incidents)

    _wait_until(all_done)

    for incident_id in incident_ids:
        detail = client.get(f"/api/incidents/{incident_id}").json()
        assert detail["incident"]["status"] == "recommendations_ready"
        assert detail["investigation"]["root_cause"]
        assert detail["recommendations"]["recommendations"]

    dash = client.get("/api/dashboard/summary").json()
    assert dash["agents"]["recommendations_ready"] == len(incident_ids)
    assert dash["agents"]["queue"] == {"queued": 0, "investigating": 0, "recommending": 0, "failed": 0}
    assert dash["analytics"]["workflow_stage_distribution"]["done"] == len(incident_ids)


def test_background_workflow_failure_does_not_stop_other_incidents(client, background_workflow):
    background_workflow.fail = True
    _ingest(client)
    incident_ids = [i["incident_id"] for i in client.get("/api/incidents").json()["incidents"]]

    def all_settled():
        incidents = client.get("/api/incidents").json()["incidents"]
        return all(i["analysis"]["state"] in ("done", "failed") for i in incidents)

    _wait_until(all_settled)
    statuses = {i["incident_id"]: i["analysis"] for i in client.get("/api/incidents").json()["incidents"]}
    assert all(s["state"] == "failed" and s["error"] for s in statuses.values())
    assert len(statuses) == len(incident_ids)  # every incident was attempted, none stuck "queued"


def test_analytics_reports_not_enabled_metrics_honestly(client):
    _ingest(client)
    analytics = client.get("/api/dashboard/summary").json()["analytics"]
    assert analytics["incident_volume"] == client.get("/api/incidents").json()["total"]
    assert sum(analytics["priority_distribution"].values()) == analytics["incident_volume"]
    assert sum(analytics["workflow_stage_distribution"].values()) == analytics["incident_volume"]
    for metric in ("mttd", "mttr", "resolution_rate", "auto_remediation_count", "human_approval_count",
                   "human_takeover_count", "verification_outcomes"):
        assert analytics[metric]["available"] is False and analytics[metric]["note"]


def test_investigate_without_vertex_config_returns_503(client, monkeypatch):
    from app.agents import runner
    from app.config.settings import Settings

    monkeypatch.setattr(runner, "settings", Settings(gcp_project=None))
    _ingest(client)
    incident_id = client.get("/api/incidents").json()["incidents"][0]["incident_id"]
    assert client.post(f"/api/incidents/{incident_id}/investigate").status_code == 503


def test_dashboard_summary(client):
    empty = client.get("/api/dashboard/summary").json()
    assert empty["ingested"] is False and empty["incidents"]["total"] == 0
    _ingest(client)
    body = client.get("/api/dashboard/summary").json()
    assert body["ingested"] is True
    assert sum(body["incidents"]["by_priority"].values()) == body["incidents"]["total"]
    assert body["top_incidents"] and body["timeline"]["points"]
    assert sum(p["requests"] for p in body["timeline"]["points"]) == 5000
