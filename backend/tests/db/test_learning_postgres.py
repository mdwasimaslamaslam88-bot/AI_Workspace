from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.learning.agent import VerifiedLessonGeneration
from app.learning.service import LearningConflictError, LearningService
from app.models.learning import LearningActivityKind, LearningReviewItem
from app.models.memory import MemoryCategory
from app.models.user import User
from app.services.memory import MemoryService


pytestmark = pytest.mark.integration


class _VerifiedTeacher:
    private_context_allowed = True

    async def generate_lesson(self, instruction: str):
        assert "BEGIN_PRIVATE_OWNER_PREFERENCES" in instruction
        assert "prefers concise worked examples" in instruction
        assert "Target/content language: ja" in instruction
        output = "Verified Japanese lesson with explanation, example, practice, and revision."
        return VerifiedLessonGeneration(
            output=output,
            output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
            model_id="test/verified-local-learning-model",
        )


@pytest.mark.asyncio
async def test_learning_flow_persists_memory_curriculum_progress_and_reviews(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    async with factory() as session:
        owner = User()
        foreign = User()
        session.add_all((owner, foreign))
        await session.commit()
        owner_id = owner.id
        foreign_id = foreign.id
        await MemoryService(session).create_for_owner(
            owner_id,
            MemoryCategory.PREFERENCE,
            "The learner prefers concise worked examples.",
        )
        service = LearningService(session, _VerifiedTeacher())
        program = await service.create_program(
            owner_id,
            subject="Japanese",
            goal="Move from zero to advanced conversation",
            target_language="ja",
            instruction_language="en",
            start_difficulty=1,
            target_difficulty=5,
            weekly_minutes=180,
            adaptive_difficulty=True,
        )
        program_id = program.id
        assert len(program.lessons) == 5
        assert [lesson.difficulty for lesson in program.lessons] == [1, 2, 3, 4, 5]
        assert await service.get_program(foreign_id, program_id) is None

        program = await service.generate_lesson(owner_id, program_id, program.lessons[0].id)
        lesson = program.lessons[0]
        assert lesson.status.value == "ready"
        assert lesson.model_id == "test/verified-local-learning-model"
        assert len(lesson.activities) == 1
        assert lesson.activities[0].kind is LearningActivityKind.REVISION
        assert "expected_answer" not in lesson.content

        program = await service.create_activity(
            owner_id,
            program_id,
            lesson.id,
            kind=LearningActivityKind.CONVERSATION,
            prompt="Reply with the greeting こんにちは",
            expected_answer="こんにちは",
            explanation="こんにちは is a common greeting.",
            difficulty=1,
            max_attempts=3,
        )
        conversation = program.lessons[0].activities[1]
        wrong = await service.submit_attempt(
            owner_id, program_id, conversation.id, "さようなら"
        )
        assert wrong.is_correct is False
        assert "こんにちは" not in wrong.feedback
        correct = await service.submit_attempt(
            owner_id, program_id, conversation.id, "こんにちは"
        )
        assert correct.is_correct is True
        persisted = await service.get_program(owner_id, program_id)
        assert persisted is not None
        assert persisted.completed_lessons == 1
        assert persisted.total_attempts == 2
        assert persisted.correct_attempts == 1

        card = await service.add_review_item(
            owner_id, program_id, front="猫", back="cat"
        )
        card_id = card.id
        with pytest.raises(LearningConflictError, match="already exists"):
            await service.add_review_item(
                owner_id, program_id, front="猫", back="duplicate"
            )
        card = await service.review_item(
            owner_id, program_id, card_id, 5, now=now
        )
        assert card.interval_days == 1
        assert card.repetitions == 1
        assert card.due_at.isoformat() == "2026-09-04T12:00:00+00:00"

    async with factory() as session:
        restored = await LearningService(session).get_program(owner_id, program_id)
        assert restored is not None
        assert restored.completed_lessons == 1
        assert restored.lessons[0].activities[1].attempts[1].answer_sha256
        assert restored.lessons[0].activities[1].attempts[1].feedback.startswith("こんにちは")
        assert restored.review_items[0].interval_days == 1


@pytest.mark.asyncio
async def test_learning_database_rejects_cross_owner_review_wiring(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User()
        foreign = User()
        session.add_all((owner, foreign))
        await session.commit()
        program = await LearningService(session).create_program(
            owner.id,
            subject="Security",
            goal="Test owner isolation",
            target_language="en",
            instruction_language="en",
            start_difficulty=1,
            target_difficulty=2,
            weekly_minutes=60,
            adaptive_difficulty=True,
        )
        session.add(
            LearningReviewItem(
                program_id=program.id,
                owner_id=foreign.id,
                front="invalid",
                back="cross-owner",
                due_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
