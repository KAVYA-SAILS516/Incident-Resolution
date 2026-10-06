"""Tests for the dashboard-wide Chat Agent (third ADK agent). Same conventions as test_api.py: the agent
runs through the real ADK Runner with a scripted model; AI output is checked for schema and properties,
never exact wording.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.agents.chat_agent import MAX_HISTORY_TURNS, build_prompt
from app.main import app
from app.schemas.chat import ChatReply, ChatTurn
from app.tools.chat_tools import make_chat_tools
from tests.conftest import REAL_LOG


@pytest.fixture
def client(store):
    with TestClient(app) as test_client:
        yield test_client


def _ingest(client):
    response = client.post("/api/logs/ingest")
    assert response.status_code == 200, response.text
    return response.json()


def test_chat_without_vertex_config_returns_503(client, monkeypatch):
    from app.agents import runner
    from app.config.settings import Settings

    monkeypatch.setattr(runner, "settings", Settings(gcp_project=None))
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 503


def test_empty_message_is_rejected(client):
    assert client.post("/api/chat", json={"message": "   "}).status_code == 400


def test_chat_answers_with_schema_and_references_real_incidents(client, fake_llm):
    _ingest(client)
    response = client.post("/api/chat", json={"message": "Which P1 incidents are open?"})
    assert response.status_code == 200, response.text
    reply = ChatReply.model_validate(response.json())
    assert reply.reply
    assert reply.referenced_incidents  # the fake model's payload references a real incident
    known_ids = {i["incident_id"] for i in client.get("/api/incidents").json()["incidents"]}
    assert set(reply.referenced_incidents) <= known_ids
    assert "list_incidents" in reply.tools_called
    assert reply.model and reply.duration_ms >= 0


def test_chat_carries_conversation_history(client, fake_llm):
    _ingest(client)
    history = [{"role": "user", "content": "How many incidents are there?"},
               {"role": "assistant", "content": "21 incidents."}]
    response = client.post("/api/chat", json={"message": "Which ones are P1?", "history": history})
    assert response.status_code == 200, response.text


def test_get_incident_summary_reports_unknown_id_instead_of_raising(store):
    tools = {t.__name__: t for t in make_chat_tools(store)}
    result = tools["get_incident_summary"]("INC-DOES-NOT-EXIST")
    assert "error" in result


def test_list_incidents_filters_and_stays_compact(store):
    from app.services.ingestion import ingest

    ingest(store)
    tools = {t.__name__: t for t in make_chat_tools(store)}
    all_rows = tools["list_incidents"]()
    assert len(all_rows) == len(store.incidents)
    assert "log_ids" not in all_rows[0] and "priority_factors" not in all_rows[0]
    p1_rows = tools["list_incidents"](priority="P1")
    assert p1_rows and all(r["priority"] == "P1" for r in p1_rows)


def test_prompt_truncates_long_history_to_the_most_recent_turns():
    history = [ChatTurn(role="user" if n % 2 == 0 else "assistant", content=f"turn {n}") for n in range(30)]
    prompt = build_prompt("latest question", history)
    assert "turn 0\n" not in prompt  # older turns dropped
    assert f"turn {29}" in prompt  # the most recent turn kept
    assert len(re.findall(r"turn \d+", prompt)) == MAX_HISTORY_TURNS  # "return" also contains "turn"
    assert "latest question" in prompt


def test_real_log_is_unchanged_after_a_chat_session(client, fake_llm):
    before = REAL_LOG.read_bytes()
    _ingest(client)
    client.post("/api/chat", json={"message": "Summarize today's incidents."})
    assert REAL_LOG.read_bytes() == before
