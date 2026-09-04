from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_os.contracts import (
    AgentKind,
    AgentPermission,
    AgentRunResult,
    AgentRunStatus,
)
from app.agent_os.policy import AgentPolicy
from app.agent_os.runtime import AgentRunManager
from app.api.dependencies import get_current_user
from app.api.v1.agent_os import router


def _api():
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    owner = SimpleNamespace(id=uuid4())
    application.dependency_overrides[get_current_user] = lambda: owner
    orchestrator = Mock(
        policy=AgentPolicy(),
        registered_specialists=(AgentKind.PLANNER, AgentKind.CODING),
        run=AsyncMock(
            return_value=AgentRunResult(
                status=AgentRunStatus.COMPLETED,
                plan=None,
                output="verified result",
                attempts=(),
            )
        ),
    )
    application.state.agent_run_manager = AgentRunManager(orchestrator)
    return TestClient(application), application, owner, orchestrator


def test_agent_os_capabilities_are_authenticated_fixed_contracts():
    client, _application, _owner, _orchestrator = _api()

    response = client.get("/api/v1/agent-os/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["profiles"]) == len(AgentKind)
    coding = next(item for item in payload["profiles"] if item["kind"] == "coding")
    assert coding["registered"] is True
    assert AgentPermission.WORKSPACE_WRITE.value in coding["permissions"]
    assert payload["persistence"] == "bounded_process_memory"
    assert payload["max_concurrency"] == 2
    assert payload["controls"] == ["pause", "resume", "approve", "modify", "retry"]


def test_agent_os_capabilities_report_bounded_queue_above_execution_concurrency():
    client, application, _owner, _orchestrator = _api()
    application.state.agent_run_manager._tasks = {
        uuid4(): Mock(done=Mock(return_value=False))
        for _ in range(9)
    }

    response = client.get("/api/v1/agent-os/capabilities")

    assert response.status_code == 200
    assert response.json()["active_runs"] == 9
    assert response.json()["max_concurrency"] == 2


def test_agent_run_api_submits_lists_reads_and_is_owner_scoped():
    client, _application, owner, orchestrator = _api()

    created = client.post(
        "/api/v1/agent-os/runs",
        json={
            "goal": "Diagnose the reproducible failure.",
            "task": "debugging",
            "source": "voice",
            "specialist": "debugging",
            "max_retries": 1,
        },
    )

    assert created.status_code == 202
    run_id = created.json()["id"]
    for _ in range(20):
        fetched = client.get(f"/api/v1/agent-os/runs/{run_id}")
        if fetched.json()["status"] == "completed":
            break
    assert fetched.status_code == 200
    assert fetched.json()["goal"] == "Diagnose the reproducible failure."
    assert fetched.json()["source"] == "voice"
    assert fetched.json()["output"] == "verified result"
    assert [event["status"] for event in fetched.json()["events"]] == [
        "queued",
        "completed",
    ]
    listed = client.get("/api/v1/agent-os/runs?limit=10")
    assert [item["id"] for item in listed.json()["items"]] == [run_id]
    submitted = orchestrator.run.await_args.args[0]
    assert submitted.permissions == frozenset({AgentPermission.MODEL_INFERENCE})
    assert submitted.source.value == "voice"

    stream = client.get(f"/api/v1/agent-os/runs/{run_id}/events?after=0")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: mission-status" in stream.text
    assert '"status":"completed"' in stream.text

    owner.id = uuid4()
    hidden = client.get(f"/api/v1/agent-os/runs/{run_id}")
    assert hidden.status_code == 404
    hidden_stream = client.get(f"/api/v1/agent-os/runs/{run_id}/events")
    assert hidden_stream.status_code == 404


def test_agent_run_api_rejects_unknown_fields_whitespace_and_unbounded_values():
    client, _application, _owner, orchestrator = _api()

    for body in (
        {"goal": " padded ", "task": "general_chat"},
        {"goal": "valid", "task": "general_chat", "max_retries": 3},
        {"goal": "valid", "task": "general_chat", "permissions": ["workspace_write"]},
    ):
        response = client.post("/api/v1/agent-os/runs", json=body)
        assert response.status_code == 422
    orchestrator.run.assert_not_awaited()


def test_agent_run_api_holds_for_approval_and_applies_audited_revision():
    client, _application, owner, orchestrator = _api()
    owner_id = owner.id
    response = client.post(
        "/api/v1/agent-os/runs",
        json={
            "goal": "Prepare a bounded plan.",
            "task": "workflow_planning",
            "require_owner_approval": True,
        },
    )
    assert response.status_code == 202
    run = response.json()
    assert run["status"] == "needs_approval"
    assert run["can_approve"] is True
    orchestrator.run.assert_not_awaited()

    revised = client.post(
        f"/api/v1/agent-os/runs/{run['id']}/modify",
        json={"goal": "Prepare and verify a bounded plan."},
    )
    assert revised.status_code == 200
    assert revised.json()["revision"] == 2
    assert revised.json()["status"] == "needs_approval"
    assert revised.json()["events"][-1]["detail_sha256"] is not None

    owner.id = uuid4()
    assert client.post(f"/api/v1/agent-os/runs/{run['id']}/approve").status_code == 404
    owner.id = owner_id
    approved = client.post(f"/api/v1/agent-os/runs/{run['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["approved"] is True
    assert approved.json()["status"] in {"queued", "completed"}
