from __future__ import annotations

import asyncio
import hashlib
import io
import logging

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.main import app
from scripts.runtime_smoke_safety import select_disposable_runtime_database


_PRIVATE_PREMISE = "A lantern keeper follows a quiet signal across a fictional moonlit harbor."


async def _clean_disposable_database() -> None:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    finally:
        await dispose_postgres(engine)


def _provision(client: TestClient, token: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/users", headers={"X-User-Provisioning-Token": token}, json={}
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _post(client: TestClient, path: str, headers: dict[str, str], payload=None) -> dict:
    response = client.post(path, headers=headers, json=payload)
    if response.status_code not in {200, 201}:
        raise RuntimeError(
            f"creative runtime request failed safely: {path} ({response.status_code})"
        )
    return response.json()


def main() -> None:
    select_disposable_runtime_database(settings)
    asyncio.run(_clean_disposable_database())
    provisioning_token = "c" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)
    try:
        with TestClient(app) as client:
            owner = _provision(client, provisioning_token)
            foreign = _provision(client, provisioning_token)
            capabilities = client.get("/api/v1/creative/capabilities", headers=owner)
            capabilities.raise_for_status()
            capability_record = capabilities.json()
            if (
                capability_record["verified_local_text_generation"] is not True
                or capability_record["general_audience_only"] is not True
                or capability_record["video_generation_status"] != "external_dependency"
                or capability_record["adult_experience_status"] != "external_dependency"
            ):
                raise RuntimeError("creative capabilities overstated a runtime boundary")
            experience = _post(
                client,
                "/api/v1/creative/experiences",
                owner,
                {
                    "mode": "story",
                    "title": "The Harbor Signal",
                    "premise": _PRIVATE_PREMISE,
                    "genre": "cozy mystery",
                    "language": "en",
                    "character_name": None,
                },
            )
            experience_id = experience["id"]
            if (
                experience["safety_tier"] != "general"
                or experience["turn_count"] != 0
                or experience["turns"] != []
            ):
                raise RuntimeError("creative experience began with fabricated output")
            if client.get(
                f"/api/v1/creative/experiences/{experience_id}", headers=foreign
            ).status_code != 404:
                raise RuntimeError("foreign owner could inspect a creative experience")
            generated = _post(
                client,
                f"/api/v1/creative/experiences/{experience_id}/turns",
                owner,
                {"owner_input": "Walk toward the signal and describe what is visible."},
            )
            if generated["turn_count"] != 1 or len(generated["turns"]) != 1:
                raise RuntimeError("creative turn did not persist exactly once")
            turn = generated["turns"][0]
            if (
                not turn["output"].strip()
                or not turn["model_id"]
                or turn["model_id"].startswith("external")
                or turn["output_sha256"]
                != hashlib.sha256(turn["output"].encode("utf-8")).hexdigest()
            ):
                raise RuntimeError("real creative verification evidence is incomplete")
            denied = client.post(
                f"/api/v1/creative/experiences/{experience_id}/turns",
                headers=owner,
                json={"owner_input": "Turn this into explicit sexual roleplay."},
            )
            if denied.status_code != 422 or "general-audience boundary" not in denied.text:
                raise RuntimeError("creative safety boundary was not enforced")
            completed = _post(
                client,
                f"/api/v1/creative/experiences/{experience_id}/complete",
                owner,
            )
            if completed["status"] != "completed" or completed["completed_at"] is None:
                raise RuntimeError("creative completion lifecycle did not persist")
            if client.post(
                f"/api/v1/creative/experiences/{experience_id}/turns",
                headers=owner,
                json={"owner_input": "Continue."},
            ).status_code != 409:
                raise RuntimeError("completed creative experience accepted another turn")
    finally:
        logging.getLogger().removeHandler(handler)

    if _PRIVATE_PREMISE in captured_logs.getvalue():
        raise RuntimeError("private creative premise leaked into logs")
    print("REAL_CREATIVE_LOCAL_STORY=passed")
    print("CREATIVE_OWNER_ISOLATION_AND_INTEGRITY=passed")
    print("CREATIVE_GENERAL_AUDIENCE_BOUNDARY=passed")
    print("ADVANCED_MEDIA_BOUNDARY=external_dependency")


if __name__ == "__main__":
    main()
