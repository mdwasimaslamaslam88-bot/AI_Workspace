from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.creative as creative_module
from app.api.dependencies import get_current_user
from app.api.v1.creative import router
from app.creative.agent import CreativeAgentError
from app.creative.safety import CreativeSafetyError
from app.db.dependencies import get_db_session
from app.models.creative import CreativeExperienceMode, CreativeExperienceStatus
from app.models.user import User


def _experience(*, with_turn: bool = False):
    now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    turns = []
    if with_turn:
        turns.append(
            SimpleNamespace(
                id=uuid4(),
                position=1,
                owner_input="Begin the story.",
                output="A verified opening scene.",
                output_sha256="a" * 64,
                model_id="ollama-local/qwen3:8b",
                created_at=now,
            )
        )
    return SimpleNamespace(
        id=uuid4(),
        mode=CreativeExperienceMode.STORY,
        title="The Quiet Observatory",
        premise="Explore a mysterious observatory with your companion.",
        genre="science fantasy",
        language="en",
        character_name=None,
        safety_tier="general",
        status=CreativeExperienceStatus.ACTIVE,
        turn_count=len(turns),
        turns=turns,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )


@pytest.fixture
def creative_api(monkeypatch):
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    service = Mock()
    for method in (
        "list_for_owner",
        "create_for_owner",
        "get_for_owner",
        "add_turn",
        "complete",
    ):
        setattr(service, method, AsyncMock())
    monkeypatch.setattr(
        creative_module, "CreativeExperienceService", Mock(return_value=service)
    )
    monkeypatch.setattr(creative_module, "_agent", lambda _request: Mock())

    async def database_override():
        yield session

    async def user_override():
        return user

    application.dependency_overrides[get_db_session] = database_override
    application.dependency_overrides[get_current_user] = user_override
    with TestClient(application) as client:
        yield client, user, service


def test_creative_api_exposes_verified_general_audience_lifecycle(creative_api):
    client, user, service = creative_api
    experience = _experience()
    continued = _experience(with_turn=True)
    service.list_for_owner.return_value = (experience,)
    service.create_for_owner.return_value = experience
    service.get_for_owner.return_value = experience
    service.add_turn.return_value = continued
    service.complete.return_value = continued

    capabilities = client.get("/api/v1/creative/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["interactive_stories"] is True
    assert capabilities.json()["general_audience_only"] is True
    assert capabilities.json()["video_generation_status"] == "external_dependency"
    assert capabilities.json()["adult_experience_status"] == "external_dependency"

    created = client.post(
        "/api/v1/creative/experiences",
        json={
            "mode": "story",
            "title": experience.title,
            "premise": experience.premise,
            "genre": experience.genre,
            "language": "en",
            "character_name": None,
        },
    )
    assert created.status_code == 201
    assert created.json()["safety_tier"] == "general"
    assert client.get("/api/v1/creative/experiences").status_code == 200
    assert client.get(f"/api/v1/creative/experiences/{experience.id}").status_code == 200

    turn = client.post(
        f"/api/v1/creative/experiences/{experience.id}/turns",
        json={"owner_input": "Begin the story."},
    )
    assert turn.status_code == 200
    assert turn.json()["turns"][0]["output_sha256"] == "a" * 64
    assert service.add_turn.await_args.args[0] == user.id
    assert client.post(
        f"/api/v1/creative/experiences/{experience.id}/complete"
    ).status_code == 200


def test_creative_api_redacts_safety_and_model_failures(creative_api):
    client, _user, service = creative_api
    experience_id = uuid4()
    service.create_for_owner.side_effect = CreativeSafetyError("PRIVATE_POLICY_DETAIL")
    denied = client.post(
        "/api/v1/creative/experiences",
        json={
            "mode": "story",
            "title": "Unsafe request",
            "premise": "A general premise",
            "genre": "fiction",
            "language": "en",
        },
    )
    assert denied.status_code == 422
    assert "PRIVATE_POLICY_DETAIL" not in denied.text

    service.add_turn.side_effect = CreativeAgentError("PRIVATE_MODEL_DETAIL")
    failed = client.post(
        f"/api/v1/creative/experiences/{experience_id}/turns",
        json={"owner_input": "Continue."},
    )
    assert failed.status_code == 502
    assert failed.json() == {"detail": "Verified local creative generation failed"}
    assert "PRIVATE_MODEL_DETAIL" not in failed.text
