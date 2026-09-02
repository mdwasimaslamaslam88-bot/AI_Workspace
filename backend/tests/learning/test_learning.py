from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_os.contracts import AgentPermission, AgentRunStatus
from app.learning.agent import LearningTeacherAgent, LearningTeacherError
from app.learning.service import (
    LearningConflictError,
    LearningService,
    answer_digest,
    normalize_answer,
)
from app.models.learning import LearningReviewItem


def _teacher(result, *, external=None):
    value = object.__new__(LearningTeacherAgent)
    value.orchestrator = SimpleNamespace(
        run=AsyncMock(return_value=result),
        model_selector=SimpleNamespace(external=external),
    )
    return value


@pytest.mark.asyncio
async def test_learning_teacher_accepts_only_untouched_verified_output():
    output = "A verified multilingual lesson with guided practice."
    digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
    result = SimpleNamespace(
        status=AgentRunStatus.COMPLETED,
        output=output,
        attempts=(
            SimpleNamespace(
                verification=SimpleNamespace(passed=True, output_sha256=digest),
                model_id="ollama-local/qwen3:8b",
            ),
        ),
    )
    teacher = _teacher(result)

    generated = await teacher.generate_lesson("Create the bounded lesson.")

    assert generated.output == output
    assert generated.output_sha256 == digest
    request = teacher.orchestrator.run.await_args.args[0]
    assert request.permissions == frozenset({AgentPermission.MODEL_INFERENCE})
    assert request.require_objective_evidence is False
    assert request.allow_external_models is False
    assert teacher.private_context_allowed is True
    assert _teacher(result, external=object()).private_context_allowed is False


@pytest.mark.asyncio
async def test_learning_teacher_rejects_mutated_digest():
    result = SimpleNamespace(
        status=AgentRunStatus.COMPLETED,
        output="changed",
        attempts=(
            SimpleNamespace(
                verification=SimpleNamespace(passed=True, output_sha256="a" * 64),
                model_id="ollama-local/qwen3:8b",
            ),
        ),
    )
    with pytest.raises(LearningTeacherError, match="digest"):
        await _teacher(result).generate_lesson("Create the bounded lesson.")


def test_learning_answer_normalization_is_unicode_case_and_space_stable():
    assert normalize_answer("Ｃａｆé   AU  LAIT") == "café au lait"
    assert answer_digest(" Base   Case ".strip()) == answer_digest("base case")


@pytest.mark.asyncio
async def test_spaced_repetition_scheduler_is_deterministic_and_bounded():
    now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    item = LearningReviewItem(
        front="猫",
        back="cat",
        interval_days=0,
        ease_milli=2500,
        repetitions=0,
        due_at=now,
    )
    session = AsyncMock()
    service = LearningService(session)
    service.repository = SimpleNamespace(
        get_review_item_for_owner=AsyncMock(return_value=item)
    )

    first = await service.review_item(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), 5, now=now
    )
    second = await service.review_item(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), 5, now=now
    )

    assert first is item and second is item
    assert item.repetitions == 2
    assert item.interval_days == 6
    assert item.ease_milli == 2700
    assert item.due_at.isoformat() == "2026-09-09T12:00:00+00:00"
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_review_item_rejects_duplicate_front_without_database_error():
    session = AsyncMock()
    service = LearningService(session)
    service.repository = SimpleNamespace(
        get_program_for_owner=AsyncMock(
            return_value=SimpleNamespace(
                review_items=[SimpleNamespace(front="base case")]
            )
        )
    )

    with pytest.raises(LearningConflictError, match="already exists"):
        await service.add_review_item(
            SimpleNamespace(), SimpleNamespace(), front="base case", back="terminates recursion"
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
