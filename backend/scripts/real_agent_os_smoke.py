from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.main import app
from scripts.runtime_smoke_safety import select_disposable_runtime_database


_GOAL = "Return one concise sentence confirming bounded local mission execution."
_TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


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


def main() -> None:
    select_disposable_runtime_database(settings)
    asyncio.run(_clean_disposable_database())
    provisioning_token = "a" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)

    try:
        with TestClient(app) as client:
            owner = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }
            foreign = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }
            started = time.monotonic()
            response = client.post(
                "/api/v1/agent-os/runs",
                headers=owner,
                json={
                    "goal": _GOAL,
                    "source": "voice",
                    "task": "general_chat",
                    "max_retries": 1,
                    "deadline_seconds": 120,
                },
            )
            if response.status_code != 202:
                raise RuntimeError("real Agent OS mission was not accepted")
            created = response.json()
            run_id = created["id"]
            if (
                created["status"] != "queued"
                or created["source"] != "voice"
                or created["events"][0]["status"] != "queued"
            ):
                raise RuntimeError("real Agent OS mission did not start truthfully")
            if client.get(
                f"/api/v1/agent-os/runs/{run_id}", headers=foreign
            ).status_code != 404:
                raise RuntimeError("foreign owner could inspect a mission")
            if client.get(
                f"/api/v1/agent-os/runs/{run_id}/events", headers=foreign
            ).status_code != 404:
                raise RuntimeError("foreign owner could stream mission activity")

            deadline = time.monotonic() + 125
            completed = created
            while time.monotonic() < deadline:
                fetched = client.get(
                    f"/api/v1/agent-os/runs/{run_id}", headers=owner
                )
                fetched.raise_for_status()
                completed = fetched.json()
                if completed["status"] in _TERMINAL:
                    break
                time.sleep(0.05)
            if completed["status"] != "completed":
                raise RuntimeError("real Agent OS mission did not complete")
            statuses = [event["status"] for event in completed["events"]]
            required = {"queued", "planning", "running", "verifying", "completed"}
            if not required.issubset(statuses):
                raise RuntimeError("real Agent OS lifecycle evidence is incomplete")
            sequences = [event["sequence"] for event in completed["events"]]
            if sequences != sorted(set(sequences)):
                raise RuntimeError("real Agent OS lifecycle sequence is invalid")
            if (
                not completed["output"].strip()
                or len(completed["plan"]) != 1
                or completed["plan"][0]["permissions"] != ["model_inference"]
                or not completed["attempts"]
                or not completed["attempts"][-1]["verified"]
            ):
                raise RuntimeError("real Agent OS verification evidence is incomplete")
            stream = client.get(
                f"/api/v1/agent-os/runs/{run_id}/events?after=0",
                headers=owner,
            )
            if (
                stream.status_code != 200
                or not stream.headers["content-type"].startswith("text/event-stream")
                or "event: mission-status" not in stream.text
                or '"status":"completed"' not in stream.text
            ):
                raise RuntimeError("real Agent OS event stream is incomplete")
            elapsed_ms = round((time.monotonic() - started) * 1000)
            print("REAL_AGENT_OS_MISSION=passed")
            print("REAL_AGENT_OS_SSE=passed")
            print(f"AGENT_OS_ATTEMPTS={len(completed['attempts'])}")
            print(f"AGENT_OS_LATENCY_MS={elapsed_ms}")
    finally:
        logging.getLogger().removeHandler(handler)

    logs = captured_logs.getvalue()
    if _GOAL in logs:
        raise RuntimeError("mission goal leaked into logs")
    print("AGENT_OS_OWNER_ISOLATION_AND_LOG_REDACTION=passed")


if __name__ == "__main__":
    main()
