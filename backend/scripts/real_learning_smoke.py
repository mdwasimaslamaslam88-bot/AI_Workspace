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


_PRIVATE_PREFERENCE = "For this private learning program, prefer concise worked examples."


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
        raise RuntimeError(f"learning runtime request failed safely: {path} ({response.status_code})")
    return response.json()


def main() -> None:
    select_disposable_runtime_database(settings)
    asyncio.run(_clean_disposable_database())
    provisioning_token = "l" * 43
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
            _post(
                client,
                "/api/v1/memories",
                owner,
                {"category": "preference", "content": _PRIVATE_PREFERENCE},
            )
            capabilities = client.get("/api/v1/learning/capabilities", headers=owner)
            capabilities.raise_for_status()
            if (
                capabilities.json()["spaced_repetition"] is not True
                or capabilities.json()["pronunciation_status"] != "external_dependency"
            ):
                raise RuntimeError("learning capabilities overstated a runtime boundary")
            program = _post(
                client,
                "/api/v1/learning/programs",
                owner,
                {
                    "subject": "Japanese",
                    "goal": "Move from zero to advanced conversation",
                    "target_language": "ja",
                    "instruction_language": "en",
                    "start_difficulty": 1,
                    "target_difficulty": 5,
                    "weekly_minutes": 180,
                    "adaptive_difficulty": True,
                },
            )
            if len(program["lessons"]) != 5 or [lesson["difficulty"] for lesson in program["lessons"]] != [1, 2, 3, 4, 5]:
                raise RuntimeError("learning curriculum progression is invalid")
            program_id = program["id"]
            if client.get(f"/api/v1/learning/programs/{program_id}", headers=foreign).status_code != 404:
                raise RuntimeError("foreign owner could inspect a learning program")
            lesson_id = program["lessons"][0]["id"]
            generated = _post(
                client,
                f"/api/v1/learning/programs/{program_id}/lessons/{lesson_id}/generate",
                owner,
            )
            lesson = generated["lessons"][0]
            if (
                lesson["status"] != "ready"
                or not lesson["model_id"]
                or lesson["output_sha256"]
                != hashlib.sha256(lesson["content"].encode("utf-8")).hexdigest()
                or lesson["memory_context_count"] < 1
                or len(lesson["activities"]) != 1
            ):
                raise RuntimeError("real learning generation evidence is incomplete")
            activity_id = lesson["activities"][0]["id"]
            wrong = _post(
                client,
                f"/api/v1/learning/programs/{program_id}/activities/{activity_id}/attempts",
                owner,
                {"answer": "Mathematics"},
            )
            if wrong["is_correct"] is not False or "Japanese" in wrong["feedback"]:
                raise RuntimeError("learning retry leaked its answer key")
            correct = _post(
                client,
                f"/api/v1/learning/programs/{program_id}/activities/{activity_id}/attempts",
                owner,
                {"answer": "Japanese"},
            )
            if correct["is_correct"] is not True or correct["score_bps"] != 10_000:
                raise RuntimeError("learning exact-answer verification failed")
            card = _post(
                client,
                f"/api/v1/learning/programs/{program_id}/review-items",
                owner,
                {"front": "猫", "back": "cat"},
            )
            reviewed = _post(
                client,
                f"/api/v1/learning/programs/{program_id}/review-items/{card['id']}/reviews",
                owner,
                {"quality": 5},
            )
            if reviewed["interval_days"] != 1 or reviewed["repetitions"] != 1:
                raise RuntimeError("learning review schedule was not persisted")
            final = client.get(f"/api/v1/learning/programs/{program_id}", headers=owner)
            final.raise_for_status()
            record = final.json()
            if record["completed_lessons"] != 1 or record["total_attempts"] != 2 or len(record["review_items"]) != 1:
                raise RuntimeError("learning progress did not persist")
    finally:
        logging.getLogger().removeHandler(handler)

    if _PRIVATE_PREFERENCE in captured_logs.getvalue():
        raise RuntimeError("private learning preference leaked into logs")
    print("REAL_LEARNING_LOCAL_TEACHER=passed")
    print("LEARNING_PROGRESS_AND_ADAPTATION=passed")
    print("LEARNING_SPACED_REPETITION=passed")
    print("PRONUNCIATION_SCORING_BOUNDARY=external_dependency")


if __name__ == "__main__":
    main()
