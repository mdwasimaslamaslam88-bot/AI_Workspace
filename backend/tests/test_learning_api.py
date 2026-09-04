from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.learning as learning_module
from app.api.dependencies import get_current_user
from app.api.v1.learning import router
from app.db.dependencies import get_db_session
from app.learning.agent import LearningTeacherError
from app.models.user import User
from app.schemas.learning import (
    LearningAttemptResponse,
    LearningProgramResponse,
    LearningReviewItemResponse,
)


def _program() -> LearningProgramResponse:
    now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    return LearningProgramResponse(
        id=uuid4(),
        subject="Japanese",
        goal="Reach advanced conversation",
        target_language="ja",
        instruction_language="en",
        start_difficulty=1,
        current_difficulty=1,
        target_difficulty=5,
        weekly_minutes=150,
        adaptive_difficulty=True,
        status="active",
        total_lessons=1,
        completed_lessons=0,
        total_attempts=0,
        correct_attempts=0,
        progress_bps=0,
        accuracy_bps=None,
        lessons=[{
            "id": uuid4(),
            "position": 1,
            "title": "Foundations: Japanese",
            "objectives": ["Recognize core vocabulary"],
            "difficulty": 1,
            "status": "planned",
            "content": None,
            "output_sha256": None,
            "model_id": None,
            "memory_context_count": 0,
            "score_bps": None,
            "activities": [],
            "created_at": now,
            "generated_at": None,
            "completed_at": None,
        }],
        review_items=[],
        created_at=now,
        updated_at=now,
        completed_at=None,
    )


@pytest.fixture
def learning_api(monkeypatch):
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    service = Mock()
    for method in (
        "list_programs",
        "create_program",
        "get_program",
        "generate_lesson",
        "generate_assessment",
        "create_activity",
        "submit_attempt",
        "request_hint",
        "add_review_item",
        "review_item",
        "update_profile",
        "attach_source",
        "detach_source",
        "start_session",
        "transition_session",
        "analytics",
        "study_plan",
        "list_events",
    ):
        setattr(service, method, AsyncMock())
    monkeypatch.setattr(learning_module, "LearningService", Mock(return_value=service))
    monkeypatch.setattr(learning_module, "_teacher", lambda _request: Mock())

    async def database_override():
        yield session

    async def user_override():
        return user

    application.dependency_overrides[get_db_session] = database_override
    application.dependency_overrides[get_current_user] = user_override
    with TestClient(application) as client:
        yield client, user, service


def test_learning_api_exposes_full_private_learning_flow(learning_api):
    client, user, service = learning_api
    program = _program()
    now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    attempt = LearningAttemptResponse(
        id=uuid4(), activity_id=uuid4(), is_correct=True, score_bps=10_000,
        feedback="Correct.", created_at=now,
    )
    review = LearningReviewItemResponse(
        id=uuid4(), front="猫", back="cat", interval_days=1, ease_milli=2_500,
        repetitions=1, due_at=now, last_quality=4, created_at=now, updated_at=now,
    )
    service.list_programs.return_value = (program,)
    service.create_program.return_value = program
    service.get_program.return_value = program
    service.generate_lesson.return_value = program
    service.create_activity.return_value = program
    service.submit_attempt.return_value = attempt
    service.add_review_item.return_value = review
    service.review_item.return_value = review

    capabilities = client.get("/api/v1/learning/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["spaced_repetition"] is True
    assert capabilities.json()["pronunciation_status"] == "external_dependency"

    created = client.post("/api/v1/learning/programs", json={
        "subject": "Japanese",
        "goal": "Reach advanced conversation",
        "target_language": "ja",
        "instruction_language": "en",
        "start_difficulty": 1,
        "target_difficulty": 5,
        "weekly_minutes": 150,
        "adaptive_difficulty": True,
    })
    assert created.status_code == 201
    assert created.json()["lessons"][0]["status"] == "planned"
    assert client.get("/api/v1/learning/programs").status_code == 200
    assert client.get(f"/api/v1/learning/programs/{program.id}").status_code == 200

    lesson_id = program.lessons[0].id
    assert client.post(
        f"/api/v1/learning/programs/{program.id}/lessons/{lesson_id}/generate"
    ).status_code == 200
    activity = client.post(
        f"/api/v1/learning/programs/{program.id}/lessons/{lesson_id}/activities",
        json={
            "kind": "quiz",
            "prompt": "Translate 猫",
            "expected_answer": "cat",
            "explanation": "猫 means cat.",
            "difficulty": 1,
            "max_attempts": 3,
        },
    )
    assert activity.status_code == 201
    assert "expected_answer" not in activity.text
    submitted = client.post(
        f"/api/v1/learning/programs/{program.id}/activities/{attempt.activity_id}/attempts",
        json={"answer": "cat"},
    )
    assert submitted.status_code == 201
    assert submitted.json()["is_correct"] is True
    assert service.submit_attempt.await_args.args[0] == user.id

    added = client.post(
        f"/api/v1/learning/programs/{program.id}/review-items",
        json={"front": "猫", "back": "cat"},
    )
    assert added.status_code == 201
    reviewed = client.post(
        f"/api/v1/learning/programs/{program.id}/review-items/{review.id}/reviews",
        json={"quality": 4},
    )
    assert reviewed.status_code == 200


def test_learning_api_hides_owner_existence_and_model_failures(learning_api):
    client, _user, service = learning_api
    program_id = uuid4()
    service.get_program.return_value = None
    missing = client.get(f"/api/v1/learning/programs/{program_id}")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Learning resource not found"}

    service.generate_lesson.side_effect = LearningTeacherError("PRIVATE_MODEL_SENTINEL")
    failed = client.post(
        f"/api/v1/learning/programs/{program_id}/lessons/{uuid4()}/generate"
    )
    assert failed.status_code == 502
    assert failed.json() == {"detail": "Verified local learning generation failed"}
    assert "PRIVATE_MODEL_SENTINEL" not in failed.text


def test_learning_api_exposes_sessions_grounding_analytics_and_hash_only_audit(learning_api):
    client, user, service = learning_api
    program = _program()
    now = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    session_id = uuid4()
    learning_session = {
        "id": session_id, "program_id": program.id, "current_lesson_id": None,
        "mode": "socratic", "status": "active", "focus": "Recursion",
        "planned_minutes": 45, "interruption_count": 0, "started_at": now,
        "last_activity_at": now, "paused_at": None, "completed_at": None,
    }
    service.update_profile.return_value = program
    service.attach_source.return_value = program
    service.detach_source.return_value = program
    service.generate_assessment.return_value = program
    service.request_hint.return_value = ("Consider the stopping condition.", 1)
    service.start_session.return_value = SimpleNamespace(**learning_session)
    service.transition_session.return_value = SimpleNamespace(**{
        **learning_session, "status": "paused", "paused_at": now,
        "interruption_count": 1,
    })
    service.analytics.return_value = {
        "program_id": program.id, "mastery_bps": 7_500, "confidence_bps": 5_000,
        "weak_topics": ["Recursion"], "due_review_count": 2,
        "current_streak_days": 3, "best_streak_days": 5,
        "active_session": SimpleNamespace(**learning_session), "skills": [],
    }
    service.study_plan.return_value = ({
        "date": now.date(), "minutes": 30, "focus": "Recursion", "mode": "revision",
    },)
    audit_id = uuid4()
    service.list_events.return_value = (SimpleNamespace(
        id=audit_id, action="session_started", entity_kind="session",
        entity_id=session_id, metadata_sha256="a" * 64, created_at=now,
    ),)

    profile = client.put(f"/api/v1/learning/programs/{program.id}/profile", json={
        "teaching_mode": "socratic", "preferences": {
            "explanation_style": "step_by_step", "hints_before_answers": True,
            "mixed_language": True, "preferred_session_minutes": 45, "pace": "balanced",
        },
    })
    assert profile.status_code == 200
    document_id, source_id = uuid4(), uuid4()
    assert client.post(f"/api/v1/learning/programs/{program.id}/sources", json={"document_id": str(document_id)}).status_code == 201
    assert client.delete(f"/api/v1/learning/programs/{program.id}/sources/{source_id}").status_code == 200
    lesson_id = program.lessons[0].id
    assert client.post(f"/api/v1/learning/programs/{program.id}/lessons/{lesson_id}/assessment").status_code == 200
    activity_id = uuid4()
    hint = client.post(f"/api/v1/learning/programs/{program.id}/activities/{activity_id}/hint")
    assert hint.json() == {"hint": "Consider the stopping condition.", "remaining": 1}
    started = client.post(f"/api/v1/learning/programs/{program.id}/sessions", json={
        "mode": "socratic", "focus": "Recursion", "planned_minutes": 45,
        "current_lesson_id": None,
    })
    assert started.status_code == 201
    assert client.post(f"/api/v1/learning/programs/{program.id}/sessions/{session_id}/pause").status_code == 200
    analytics = client.get(f"/api/v1/learning/programs/{program.id}/analytics")
    assert analytics.json()["weak_topics"] == ["Recursion"]
    assert client.get(f"/api/v1/learning/programs/{program.id}/study-plan?days=1").status_code == 200
    audit = client.get(f"/api/v1/learning/programs/{program.id}/audit")
    assert audit.json()["items"][0]["metadata_sha256"] == "a" * 64
    assert "expected_answer" not in audit.text
    assert service.start_session.await_args.args[0] == user.id
