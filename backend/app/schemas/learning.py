from __future__ import annotations

from datetime import datetime
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.learning import (
    LearningActivityKind,
    LearningLessonStatus,
    LearningProgramStatus,
)


_TEXT_PATTERN = r"^\S(?:[\s\S]*\S)?$"
_LANGUAGE_PATTERN = r"^[A-Za-z][A-Za-z0-9-]{1,34}$"


class LearningProgramCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=160, pattern=_TEXT_PATTERN)
    goal: str = Field(min_length=1, max_length=2_000, pattern=_TEXT_PATTERN)
    target_language: str = Field(pattern=_LANGUAGE_PATTERN)
    instruction_language: str = Field(pattern=_LANGUAGE_PATTERN)
    start_difficulty: int = Field(default=1, strict=True, ge=1, le=5)
    target_difficulty: int = Field(default=5, strict=True, ge=1, le=5)
    weekly_minutes: int = Field(default=150, strict=True, ge=15, le=10_080)
    adaptive_difficulty: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def progression_is_forward(self):
        if self.start_difficulty > self.target_difficulty:
            raise ValueError("start difficulty cannot exceed target difficulty")
        return self


class LearningActivityCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LearningActivityKind
    prompt: str = Field(min_length=1, max_length=4_000, pattern=_TEXT_PATTERN)
    expected_answer: str = Field(min_length=1, max_length=4_000, pattern=_TEXT_PATTERN)
    explanation: str = Field(min_length=1, max_length=4_000, pattern=_TEXT_PATTERN)
    difficulty: int = Field(strict=True, ge=1, le=5)
    max_attempts: int = Field(default=3, strict=True, ge=1, le=10)


class LearningAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=4_000, pattern=_TEXT_PATTERN)


class LearningReviewItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    front: str = Field(min_length=1, max_length=1_000, pattern=_TEXT_PATTERN)
    back: str = Field(min_length=1, max_length=2_000, pattern=_TEXT_PATTERN)


class LearningReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality: int = Field(strict=True, ge=0, le=5)


class LearningAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    activity_id: UUID
    is_correct: bool
    score_bps: int
    feedback: str
    created_at: datetime


class LearningActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lesson_id: UUID
    kind: LearningActivityKind
    prompt: str
    explanation_available_after_attempt: bool = True
    difficulty: int
    max_attempts: int
    attempts: list[LearningAttemptResponse]
    created_at: datetime


class LearningLessonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    title: str
    objectives: list[str]
    difficulty: int
    status: LearningLessonStatus
    content: str | None
    output_sha256: str | None
    model_id: str | None
    memory_context_count: int
    score_bps: int | None
    activities: list[LearningActivityResponse]
    created_at: datetime
    generated_at: datetime | None
    completed_at: datetime | None

    @model_validator(mode="before")
    @classmethod
    def decode_persisted_fields(cls, value):
        if isinstance(value, dict):
            return value
        objectives = json.loads(value.objectives_json)
        memory_ids = json.loads(value.memory_ids_json)
        if not isinstance(objectives, list) or not all(isinstance(item, str) for item in objectives):
            raise ValueError("persisted learning objectives are invalid")
        if not isinstance(memory_ids, list) or not all(isinstance(item, str) for item in memory_ids):
            raise ValueError("persisted learning memory context is invalid")
        return {
            "id": value.id,
            "position": value.position,
            "title": value.title,
            "objectives": objectives,
            "difficulty": value.difficulty,
            "status": value.status,
            "content": value.content,
            "output_sha256": value.output_sha256,
            "model_id": value.model_id,
            "memory_context_count": len(memory_ids),
            "score_bps": value.score_bps,
            "activities": value.activities,
            "created_at": value.created_at,
            "generated_at": value.generated_at,
            "completed_at": value.completed_at,
        }


class LearningReviewItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    front: str
    back: str
    interval_days: int
    ease_milli: int
    repetitions: int
    due_at: datetime
    last_quality: int | None
    created_at: datetime
    updated_at: datetime


class LearningProgramResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject: str
    goal: str
    target_language: str
    instruction_language: str
    start_difficulty: int
    current_difficulty: int
    target_difficulty: int
    weekly_minutes: int
    adaptive_difficulty: bool
    status: LearningProgramStatus
    total_lessons: int
    completed_lessons: int
    total_attempts: int
    correct_attempts: int
    progress_bps: int
    accuracy_bps: int | None
    lessons: list[LearningLessonResponse]
    review_items: list[LearningReviewItemResponse]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @model_validator(mode="before")
    @classmethod
    def derive_progress(cls, value):
        if isinstance(value, dict):
            return value
        return {
            "id": value.id,
            "subject": value.subject,
            "goal": value.goal,
            "target_language": value.target_language,
            "instruction_language": value.instruction_language,
            "start_difficulty": value.start_difficulty,
            "current_difficulty": value.current_difficulty,
            "target_difficulty": value.target_difficulty,
            "weekly_minutes": value.weekly_minutes,
            "adaptive_difficulty": value.adaptive_difficulty,
            "status": value.status,
            "total_lessons": value.total_lessons,
            "completed_lessons": value.completed_lessons,
            "total_attempts": value.total_attempts,
            "correct_attempts": value.correct_attempts,
            "progress_bps": value.completed_lessons * 10_000 // value.total_lessons,
            "accuracy_bps": (
                value.correct_attempts * 10_000 // value.total_attempts
                if value.total_attempts
                else None
            ),
            "lessons": value.lessons,
            "review_items": value.review_items,
            "created_at": value.created_at,
            "updated_at": value.updated_at,
            "completed_at": value.completed_at,
        }


class LearningProgramPageResponse(BaseModel):
    items: list[LearningProgramResponse]


class LearningCapabilitiesResponse(BaseModel):
    teacher_mode: bool = True
    speaking_partner: bool = True
    exam_mode: bool = True
    vocabulary_trainer: bool = True
    spaced_repetition: bool = True
    pronunciation_scoring: bool = False
    pronunciation_status: str = "external_dependency"
    pronunciation_dependencies: list[str] = Field(
        default_factory=lambda: ["pronunciation_scoring_provider"]
    )
