import os

import pytest
from fastapi.testclient import TestClient

from app.application import parser
from app.application.discovery import discover_application
from app.application.knowledge import answer_question, service_context
from app.application.reader import ApplicationPathError, collect_files, validate_application_path
from app.main import app
from tests.mini_shop import make_app


@pytest.fixture
def shop(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def knowledge(shop):
    return discover_application("Shop", str(shop))


# --- path validation ---
def test_rejects_missing_empty_and_file_paths(tmp_path, shop):
    for bad in ("", "   ", str(tmp_path / "nope")):
        with pytest.raises(ApplicationPathError):
            validate_application_path(bad)
    with pytest.raises(ApplicationPathError, match="not a directory"):
        validate_application_path(str(shop / "compose.yaml"))


def test_allowed_root_blocks_traversal(tmp_path, shop):
    assert validate_application_path(str(shop), tmp_path) == shop.resolve()
    with pytest.raises(ApplicationPathError, match="outside"):
        validate_application_path(str(tmp_path.parent), tmp_path)
    with pytest.raises(ApplicationPathError, match="outside"):
        validate_application_path(str(shop / ".." / ".."), tmp_path)


# --- discovery ---
def test_services_criticality_and_kind_come_from_the_files(knowledge):
    names = {s.name: s for s in knowledge.services}
    assert names["payment"].criticality == "CRITICAL" and names["payment"].kind == "application"
    assert names["email"].criticality == "MEDIUM"
    assert names["reports"].criticality is None  # not declared -> unknown, never guessed
    assert names["flagd"].kind == "infrastructure"
    assert knowledge.criticality["payment"] == "CRITICAL"


def test_dependencies_from_depends_on_and_env_references(knowledge):
    names = {s.name: s for s in knowledge.services}
    assert names["checkout"].dependencies == ["email", "payment"]
    assert names["payment"].dependents == ["checkout"]
    assert "flagd" in names["payment"].dependencies
    assert "otel-collector" not in names["payment"].dependencies  # telemetry plumbing is not a dependency


def test_documentation_purpose_and_unknown_purpose(knowledge):
    names = {s.name: s for s in knowledge.services}
    assert names["payment"].purpose == "This service is responsible for processing payments."
    assert names["reports"].purpose is None
    assert knowledge.summary == "A small demo shop used in tests."
    paths = {d.path for d in knowledge.documentation}
    assert {"README.md", "docs/architecture.md", "src/payment/README.md"} <= paths
    assert not any("node_modules" in p for p in paths)


def test_apis_failure_scenarios_and_telemetry(knowledge):
    assert {a.name for a in knowledge.apis} == {"PaymentService/Charge", "EmailService/SendOrderConfirmation"}
    assert next(s for s in knowledge.services if s.name == "payment").apis == ["PaymentService/Charge"]
    assert [(f.name, f.service) for f in knowledge.failure_scenarios] == [("paymentFailure", "payment")]
    assert set(knowledge.telemetry["signals"]) == {"logs", "metrics", "traces"}


def test_unsupported_ignored_and_never_executed(shop, knowledge):
    names = {p.name for p in collect_files(shop.resolve()).files}
    assert "binary.bin" not in names and "main.js" not in names
    assert knowledge.files_scanned >= 8


def test_large_files_and_directories_are_bounded(tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    (root / "huge.yaml").write_text("a: " + "x" * 2000, encoding="utf-8")
    for i in range(30):
        (root / f"f{i}.md").write_text(f"# t{i}\n\ntext", encoding="utf-8")
    capped = collect_files(root.resolve(), max_files=10, max_bytes=1000)
    assert capped.truncated and len(capped.files) == 10
    found = collect_files(root.resolve(), max_files=100, max_bytes=1000)
    assert any("huge.yaml" in s and "larger" in s for s in found.skipped)


def test_symlink_outside_root_is_skipped(tmp_path, shop):
    secret = tmp_path / "secret.md"
    secret.write_text("# Secret\n\ntop secret", encoding="utf-8")
    try:
        os.symlink(secret, shop / "leak.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    knowledge = discover_application("Shop", str(shop))
    assert "leak.md" not in {d.path for d in knowledge.documentation}
    assert any("symlink outside root" in w for w in knowledge.scan_warnings)


def test_empty_directory_warns_instead_of_inventing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    knowledge = discover_application("Empty", str(empty))
    assert knowledge.services == [] and knowledge.telemetry["signals"] == {}
    assert any("No service definitions" in w for w in knowledge.scan_warnings)


def test_parsers_are_safe_on_garbage():
    assert parser.parse_compose(":::not yaml{", "x") == {}
    assert parser.parse_flags("not json", "x") == []
    assert parser.parse_proto("nothing", "x") == []
    assert parser.parse_markdown("") == {"title": None, "summary": None}


# --- knowledge answers ---
def test_knowledge_answers_from_the_model(knowledge):
    assert "processing payments" in answer_question(knowledge, "What does Payment do?")["answer"]
    assert "flagd" in answer_question(knowledge, "What does payment depend on?")["answer"]
    assert "payment (CRITICAL)" in answer_question(knowledge, "Which services are critical?")["answer"]
    assert "paymentFailure" in answer_question(knowledge, "What failure scenarios are documented?")["answer"]
    assert "logs" in answer_question(knowledge, "What telemetry exists?")["answer"]
    assert "PaymentService/Charge" in answer_question(knowledge, "What APIs exist?")["answer"]
    assert "No application has been scanned" in answer_question(None, "What services exist?")["answer"]
    assert service_context(knowledge, "nonexistent") is None


# --- API ---
def test_scan_api(store, shop):
    client = TestClient(app)
    bad = client.post("/api/applications/scan", json={"name": "Shop", "path": str(shop / "missing")})
    assert bad.status_code == 400
    ok = client.post("/api/applications/scan", json={"name": "Shop", "path": str(shop)})
    assert ok.status_code == 200
    body = ok.json()
    assert body["scanned"] and {p["priority"] for p in body["policies"]} == {"P1", "P2", "P3", "P4"}
    assert store.application.application_name == "Shop"
    assert client.get("/api/applications").json()["knowledge"]["application_name"] == "Shop"
    answer = client.post("/api/knowledge/ask", json={"question": "what does payment do"}).json()["answer"]
    assert "processing payments" in answer
