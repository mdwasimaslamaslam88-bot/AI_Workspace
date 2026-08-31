from pathlib import Path
import subprocess
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.api.v1.self_update import router
from app.maintenance import REQUIRED_UPDATE_GATES, SelfUpdateManager, ValidationGate


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ("/usr/bin/git", *arguments),
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )


def _client(manager=None) -> TestClient:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    application.state.self_update_manager = manager
    return TestClient(application)


def test_update_status_is_sanitized_and_unconfigured_by_default():
    response = _client().get("/api/v1/updates/status")

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "status": "idle",
        "version": None,
        "candidate_commit": None,
        "checkpoint_ready": False,
        "rollback_ready": False,
        "activation_requires_owner": False,
        "gates": [],
        "failure_code": None,
    }


def test_owner_can_cancel_only_after_all_update_gates_pass(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "API Test")
    _git(repository, "config", "user.email", "api@example.invalid")
    (repository / "app.txt").write_text("ready\n", encoding="utf-8")
    _git(repository, "add", "app.txt")
    _git(repository, "commit", "-m", "ready")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)

    def passing(gate, _candidate):
        return subprocess.CompletedProcess(gate.command, 0, "private output", "")

    manager = SelfUpdateManager(repository, state_root, gate_runner=passing)
    manager.prepare(
        candidate_ref="HEAD",
        version="2.0.0",
        gates=tuple(
            ValidationGate(name, ("/usr/bin/true",))
            for name in sorted(REQUIRED_UPDATE_GATES)
        ),
    )
    client = _client(manager)

    ready = client.get("/api/v1/updates/status")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["activation_requires_owner"] is True
    assert ready.json()["rollback_ready"] is True
    assert {gate["name"] for gate in ready.json()["gates"]} == REQUIRED_UPDATE_GATES
    assert all(gate["passed"] for gate in ready.json()["gates"])
    assert "private output" not in ready.text

    cancelled = client.post(
        "/api/v1/updates/decision",
        json={"decision": "cancel"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    repeated = client.post(
        "/api/v1/updates/decision",
        json={"decision": "cancel"},
    )
    assert repeated.status_code == 409
