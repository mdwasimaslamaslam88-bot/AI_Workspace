from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.creative.agent import VerifiedCreativeGeneration
from app.creative.service import CreativeExperienceService
from app.models.creative import (
    CreativeExperienceMode,
    CreativeExperienceStatus,
    CreativeTurn,
)
from app.models.user import User


pytestmark = pytest.mark.integration


class _VerifiedCreativeAgent:
    async def generate(self, instruction: str):
        assert "BEGIN_UNTRUSTED_CREATIVE_HISTORY" in instruction
        assert "BEGIN_OWNER_CREATIVE_TURN" in instruction
        assert "Mode: story" in instruction
        output = "The observatory wakes beneath the stars. Which room will you inspect?"
        return VerifiedCreativeGeneration(
            output=output,
            output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
            model_id="test/verified-local-creative-model",
        )


@pytest.mark.asyncio
async def test_creative_experience_persists_verified_turns_and_owner_isolation(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User()
        foreign = User()
        session.add_all((owner, foreign))
        await session.commit()
        owner_id = owner.id
        foreign_id = foreign.id
        service = CreativeExperienceService(session, _VerifiedCreativeAgent())
        experience = await service.create_for_owner(
            owner_id,
            mode=CreativeExperienceMode.STORY,
            title="The Quiet Observatory",
            premise="Explore a mysterious observatory with a trusted fictional companion.",
            genre="science fantasy",
            language="en",
            character_name=None,
        )
        experience_id = experience.id
        assert experience.safety_tier == "general"
        assert await service.get_for_owner(foreign_id, experience_id) is None

        experience = await service.add_turn(
            owner_id, experience_id, "Begin at the locked map room."
        )
        assert experience.turn_count == 1
        assert experience.turns[0].position == 1
        assert experience.turns[0].model_id == "test/verified-local-creative-model"
        assert experience.turns[0].output_sha256 == hashlib.sha256(
            experience.turns[0].output.encode("utf-8")
        ).hexdigest()
        experience = await service.complete(owner_id, experience_id)
        assert experience.status is CreativeExperienceStatus.COMPLETED
        assert experience.completed_at is not None

    async with factory() as session:
        restored = await CreativeExperienceService(session).get_for_owner(
            owner_id, experience_id
        )
        assert restored is not None
        assert restored.turn_count == 1
        assert restored.turns[0].owner_input == "Begin at the locked map room."


@pytest.mark.asyncio
async def test_creative_database_rejects_cross_owner_turn_wiring(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User()
        foreign = User()
        session.add_all((owner, foreign))
        await session.commit()
        experience = await CreativeExperienceService(session).create_for_owner(
            owner.id,
            mode=CreativeExperienceMode.GAME,
            title="Clockwork Maze",
            premise="Navigate a family-friendly puzzle maze.",
            genre="puzzle",
            language="en",
            character_name=None,
        )
        output = "A safe result."
        session.add(
            CreativeTurn(
                experience_id=experience.id,
                owner_id=foreign.id,
                position=1,
                owner_input="Go north.",
                output=output,
                output_sha256=hashlib.sha256(output.encode()).hexdigest(),
                model_id="test/local",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
