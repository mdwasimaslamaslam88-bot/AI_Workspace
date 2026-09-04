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
                or capabilities.json()["adaptive_assessment"] is not True
                or capabilities.json()["document_grounding"] is not True
                or capabilities.json()["resumable_sessions"] is not True
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
                    "teaching_mode": "socratic",
                    "preferences": {
                        "explanation_style": "example_first",
                        "hints_before_answers": True,
                        "mixed_language": True,
                        "preferred_session_minutes": 45,
                        "pace": "balanced",
                    },
                },
            )
            if len(program["lessons"]) != 5 or [lesson["difficulty"] for lesson in program["lessons"]] != [1, 2, 3, 4, 5]:
                raise RuntimeError("learning curriculum progression is invalid")
            program_id = program["id"]
            if client.get(f"/api/v1/learning/programs/{program_id}", headers=foreign).status_code != 404:
                raise RuntimeError("foreign owner could inspect a learning program")
            lesson_id = program["lessons"][0]["id"]
            study_session = _post(
                client,
                f"/api/v1/learning/programs/{program_id}/sessions",
                owner,
                {
                    "mode": "focus",
                    "focus": "Japanese foundations",
                    "planned_minutes": 45,
                    "current_lesson_id": lesson_id,
                },
            )
            for action, expected_status in (
                ("pause", "paused"),
                ("resume", "active"),
                ("complete", "completed"),
            ):
                study_session = _post(
                    client,
                    f"/api/v1/learning/programs/{program_id}/sessions/{study_session['id']}/{action}",
                    owner,
                )
                if study_session["status"] != expected_status:
                    raise RuntimeError("learning session recovery state is invalid")
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
            required_activities = []
            for prompt, answer, skill_name in (
                ("What language is the subject of this course?", "Japanese", "Subject recognition"),
                ("Translate 猫 into English.", "cat", "Vocabulary"),
                ("Write the romanized Japanese greeting for hello.", "konnichiwa", "Conversation"),
            ):
                assessed = _post(
                    client,
                    f"/api/v1/learning/programs/{program_id}/lessons/{lesson_id}/activities",
                    owner,
                    {
                        "kind": "short_answer",
                        "prompt": prompt,
                        "expected_answer": answer,
                        "explanation": f"The verified answer is {answer}.",
                        "difficulty": 1,
                        "max_attempts": 3,
                        "skill_name": skill_name,
                        "required": True,
                    },
                )
                required_activities.append(
                    next(
                        activity
                        for activity in assessed["lessons"][0]["activities"]
                        if activity["prompt"] == prompt
                    )
                )
            activity_id = required_activities[0]["id"]
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
            for activity, answer in zip(
                required_activities[1:], ("cat", "konnichiwa"), strict=True
            ):
                passed = _post(
                    client,
                    f"/api/v1/learning/programs/{program_id}/activities/{activity['id']}/attempts",
                    owner,
                    {"answer": answer},
                )
                if passed["is_correct"] is not True:
                    raise RuntimeError("learning required assessment did not pass")
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
            progress_evidence = {
                "completed_lessons": record["completed_lessons"],
                "total_attempts": record["total_attempts"],
                "review_items": len(record["review_items"]),
                "current_difficulty": record["current_difficulty"],
            }
            if progress_evidence != {
                "completed_lessons": 1,
                "total_attempts": 4,
                "review_items": 1,
                "current_difficulty": 2,
            }:
                raise RuntimeError(
                    f"learning progress did not persist: {progress_evidence}"
                )
            analytics = client.get(
                f"/api/v1/learning/programs/{program_id}/analytics", headers=owner
            )
            analytics.raise_for_status()
            if (
                analytics.json()["active_session"] is not None
                or analytics.json()["current_streak_days"] < 1
                or analytics.json()["mastery_bps"] != 7_500
            ):
                raise RuntimeError("learning analytics did not reflect verified state")
            study_plan = client.get(
                f"/api/v1/learning/programs/{program_id}/study-plan",
                headers=owner,
                params={"days": 7},
            )
            study_plan.raise_for_status()
            if len(study_plan.json()["items"]) != 7:
                raise RuntimeError("weekly learning plan is incomplete")
            audit = client.get(
                f"/api/v1/learning/programs/{program_id}/audit", headers=owner
            )
            audit.raise_for_status()
            audit_actions = {item["action"] for item in audit.json()["items"]}
            if not {
                "program_created",
                "lesson_generated",
                "attempt_submitted",
                "review_completed",
                "session_started",
                "session_paused",
                "session_resumed",
                "session_completed",
            }.issubset(audit_actions):
                raise RuntimeError("learning state audit is incomplete")
            if any(len(item["metadata_sha256"]) != 64 for item in audit.json()["items"]):
                raise RuntimeError("learning audit metadata is not hash-only")
    finally:
        logging.getLogger().removeHandler(handler)

    if _PRIVATE_PREFERENCE in captured_logs.getvalue():
        raise RuntimeError("private learning preference leaked into logs")
    print("REAL_LEARNING_LOCAL_TEACHER=passed")
    print("LEARNING_PROGRESS_AND_ADAPTATION=passed")
    print("LEARNING_SPACED_REPETITION=passed")
    print("LEARNING_SESSION_RECOVERY=passed")
    print("LEARNING_ANALYTICS_AND_AUDIT=passed")
    print("PRONUNCIATION_SCORING_BOUNDARY=external_dependency")


if __name__ == "__main__":
    main()
