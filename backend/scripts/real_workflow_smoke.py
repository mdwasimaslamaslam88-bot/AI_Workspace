from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.main import app


_MEMORY = "My private workflow marker is Quiet Juniper."
_TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


def _require_disposable_database() -> None:
    if settings.DATABASE_URL is None and settings.TEST_DATABASE_URL is not None:
        settings.DATABASE_URL = settings.TEST_DATABASE_URL
    database_url = settings.DATABASE_URL
    if database_url is None:
        raise RuntimeError("DATABASE_URL must select the disposable test database")
    parsed = make_url(str(database_url))
    if parsed.host != "127.0.0.1" or parsed.database != "ai_workspace_test":
        raise RuntimeError("workflow smoke is restricted to the disposable test DB")


async def _clean_disposable_database() -> None:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    finally:
        await dispose_postgres(engine)


def _provision(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/users",
        headers={"X-User-Provisioning-Token": token},
        json={},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _create(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/workflows",
        headers=headers,
        json={"name": name, "steps": steps},
    )
    response.raise_for_status()
    body = response.json()
    if (
        body["status"] != "pending"
        or body["step_count"] != len(steps)
        or body["current_step_position"] is not None
        or body["cancel_requested"] is not False
    ):
        raise RuntimeError("workflow did not start in a deterministic pending state")
    return body


def _start_and_wait(
    client: TestClient,
    headers: dict[str, str],
    workflow_id: str,
    *,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    started = client.post(
        f"/api/v1/workflows/{workflow_id}/start", headers=headers
    )
    if started.status_code != 202:
        raise RuntimeError("workflow did not accept a bounded start request")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/v1/workflows/{workflow_id}", headers=headers
        )
        response.raise_for_status()
        body = response.json()
        if body["status"] in _TERMINAL:
            return body
        time.sleep(0.025)
    raise RuntimeError("workflow exceeded the real smoke deadline")


def main() -> None:
    _require_disposable_database()
    asyncio.run(_clean_disposable_database())
    provisioning_token = "w" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)

    try:
        with TestClient(app) as client:
            owner_headers = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }
            foreign_headers = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }
            memory = client.post(
                "/api/v1/memories",
                headers=owner_headers,
                json={"category": "fact", "content": _MEMORY},
            )
            memory.raise_for_status()

            workflow = _create(
                client,
                owner_headers,
                "Owner-safe local workflow",
                [
                    {
                        "tool_name": "calculator",
                        "arguments": {"expression": "9*9"},
                    },
                    {
                        "tool_name": "local_time",
                        "arguments": {"timezone": "Asia/Kolkata"},
                    },
                    {
                        "tool_name": "memory_search",
                        "arguments": {"query": "What is my workflow marker?"},
                    },
                ],
            )
            workflow_id = workflow["id"]
            if [step["permission"] for step in workflow["steps"]] != [
                "utility",
                "utility",
                "personal_memory_read",
            ]:
                raise RuntimeError("workflow did not persist step permissions")

            for method in ("get", "post", "delete"):
                request = getattr(client, method)
                suffix = "/start" if method == "post" else ""
                response = request(
                    f"/api/v1/workflows/{workflow_id}{suffix}",
                    headers=foreign_headers,
                )
                if response.status_code != 404:
                    raise RuntimeError("foreign owner could access a workflow")

            completed = _start_and_wait(
                client, owner_headers, workflow_id
            )
            if (
                completed["status"] != "completed"
                or completed["error_code"] is not None
                or completed["completed_at"] is None
                or any(step["status"] != "completed" for step in completed["steps"])
            ):
                raise RuntimeError("workflow did not reach a complete terminal state")
            results = [step["result"] for step in completed["steps"]]
            if results[0] != {"value": 81}:
                raise RuntimeError("workflow calculator result was incorrect")
            if results[1].get("utc_offset") != "+0530":
                raise RuntimeError("workflow local time result was incorrect")
            if not any(
                "Quiet Juniper" in item["content"]
                for item in results[2]["items"]
            ):
                raise RuntimeError("workflow memory step crossed or missed owner data")

            executions = client.get(
                "/api/v1/tools/executions", headers=owner_headers
            )
            executions.raise_for_status()
            audit_items = executions.json()["items"]
            step_execution_ids = {
                step["tool_execution_id"] for step in completed["steps"]
            }
            if (
                len(audit_items) != 3
                or any(item["initiator"] != "workflow" for item in audit_items)
                or {item["id"] for item in audit_items} != step_execution_ids
            ):
                raise RuntimeError("workflow steps lack exact tool audit linkage")

            restart = client.post(
                f"/api/v1/workflows/{workflow_id}/start", headers=owner_headers
            )
            if restart.status_code != 409:
                raise RuntimeError("terminal workflow accepted a second start")

            cancelled = _create(
                client,
                owner_headers,
                "Cancelled before execution",
                [
                    {
                        "tool_name": "calculator",
                        "arguments": {"expression": "1+1"},
                    }
                ],
            )
            cancelled_response = client.delete(
                f"/api/v1/workflows/{cancelled['id']}", headers=owner_headers
            )
            cancelled_response.raise_for_status()
            cancelled_body = cancelled_response.json()
            if (
                cancelled_body["status"] != "cancelled"
                or cancelled_body["error_code"] != "workflow_cancelled"
                or cancelled_body["completed_at"] is None
            ):
                raise RuntimeError("pending cancellation was not deterministic")

            failing = _create(
                client,
                owner_headers,
                "Safely failed step",
                [
                    {
                        "tool_name": "local_time",
                        "arguments": {"timezone": "Invalid/Nowhere"},
                    }
                ],
            )
            failed = _start_and_wait(client, owner_headers, failing["id"])
            if (
                failed["status"] != "failed"
                or failed["error_code"] != "step_failed"
                or failed["steps"][0]["status"] != "failed"
                or failed["steps"][0]["result"] is not None
            ):
                raise RuntimeError("failed step did not terminate safely")

            invalid = client.post(
                "/api/v1/workflows",
                headers=owner_headers,
                json={
                    "name": "Forbidden shell",
                    "steps": [
                        {"tool_name": "shell", "arguments": {"command": "id"}}
                    ],
                },
            )
            if invalid.status_code != 422:
                raise RuntimeError("workflow accepted an unregistered tool")
            too_many = client.post(
                "/api/v1/workflows",
                headers=owner_headers,
                json={
                    "name": "Unbounded",
                    "steps": [
                        {
                            "tool_name": "calculator",
                            "arguments": {"expression": "1+1"},
                        }
                        for _ in range(9)
                    ],
                },
            )
            if too_many.status_code != 422:
                raise RuntimeError("workflow accepted more than eight steps")

            owner_list = client.get("/api/v1/workflows", headers=owner_headers)
            foreign_list = client.get("/api/v1/workflows", headers=foreign_headers)
            owner_list.raise_for_status()
            foreign_list.raise_for_status()
            if len(owner_list.json()["items"]) != 3:
                raise RuntimeError("owner workflow execution history is incomplete")
            if foreign_list.json()["items"]:
                raise RuntimeError("workflow list crossed an owner boundary")

        if _MEMORY in captured_logs.getvalue():
            raise RuntimeError("private workflow tool output reached logs")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        asyncio.run(_clean_disposable_database())

    print("REAL_WORKFLOW_SMOKE=passed")
    print("BOUNDED_STEPS_WALL_CLOCK_AND_TERMINAL_STATES=passed")
    print("OWNER_ISOLATION_PERMISSION_AND_AUDIT_LINKAGE=passed")
    print("CANCELLATION_FAILURE_AND_RESTART_SAFETY=passed")


if __name__ == "__main__":
    main()
