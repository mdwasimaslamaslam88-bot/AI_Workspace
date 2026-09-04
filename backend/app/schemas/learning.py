from __future__ import annotations

from datetime import date, datetime
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.learning import (
    LearningActivityKind,
    LearningGradingMode,
    LearningLessonStatus,
    LearningProgramStatus,
    LearningSessionStatus,
    LearningTeachingMode,
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
    teaching_mode: LearningTeachingMode = LearningTeachingMode.TEACHER
    preferences: "LearningPreferences" = Field(default_factory=lambda: LearningPreferences())
    source_document_ids: list[UUID] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def progression_is_forward(self):
        if self.start_difficulty > self.target_difficulty:
            raise ValueError("start difficulty cannot exceed target difficulty")
        return self


class LearningPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explanation_style: str = Field(default="step_by_step", pattern=r"^(concise|detailed|step_by_step|example_first)$")
    hints_before_answers: bool = Field(default=True, strict=True)
    mixed_language: bool = Field(default=False, strict=True)
    preferred_session_minutes: int = Field(default=30, strict=True, ge=5, le=480)
    pace: str = Field(default="balanced", pattern=r"^(gentle|balanced|intensive)$")


class LearningProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teaching_mode: LearningTeachingMode
    preferences: LearningPreferences


class LearningActivityCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LearningActivityKind
    prompt: str = Field(min_length=1, max_length=4_000, pattern=_TEXT_PATTERN)
    expected_answer: str = Field(min_length=1, max_length=4_000, pattern=_TEXT_PATTERN)
    explanation: str = Field(min_length=1, max_length=4_000, pattern=_TEXT_PATTERN)
    difficulty: int = Field(strict=True, ge=1, le=5)
    max_attempts: int = Field(default=3, strict=True, ge=1, le=10)
    skill_name: str = Field(default="General", min_length=1, max_length=160, pattern=_TEXT_PATTERN)
    grading_mode: LearningGradingMode = LearningGradingMode.EXACT
    hints: list[str] = Field(default_factory=list, max_length=10)
    rubric_keywords: list[str] = Field(default_factory=list, max_length=12)
    source_ids: list[UUID] = Field(default_factory=list, max_length=8)
    required: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def grading_contract_is_consistent(self):
        if (self.grading_mode is LearningGradingMode.RUBRIC) != bool(self.rubric_keywords):
            raise ValueError("rubric grading requires rubric keywords")
        return self


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


class LearningSourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID


class LearningSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: LearningTeachingMode
    focus: str = Field(min_length=1, max_length=500, pattern=_TEXT_PATTERN)
    planned_minutes: int = Field(strict=True, ge=5, le=480)
    current_lesson_id: UUID | None = None


class LearningHintResponse(BaseModel):
    hint: str
    remaining: int = Field(ge=0, le=10)


class LearningAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    activity_id: UUID
    is_correct: bool
    score_bps: int
    feedback: str
    mistake_code: str | None = None
    created_at: datetime


class LearningActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lesson_id: UUID
    kind: LearningActivityKind
    grading_mode: LearningGradingMode = LearningGradingMode.EXACT
    prompt: str
    explanation_available_after_attempt: bool = True
    difficulty: int
    max_attempts: int
    skill_name: str = "General"
    hints_available: int = 0
    hints_requested: int = 0
    source_context_count: int = 0
    required: bool = True
    generated: bool = False
    model_id: str | None = None
    attempts: list[LearningAttemptResponse]
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def decode_activity_fields(cls, value):
        if isinstance(value, dict):
            return value
        hints = json.loads(value.hints_json)
        sources = json.loads(value.source_ids_json)
        if not isinstance(hints, list) or not isinstance(sources, list):
            raise ValueError("persisted learning activity context is invalid")
        return {
            "id": value.id,
            "lesson_id": value.lesson_id,
            "kind": value.kind,
            "grading_mode": value.grading_mode,
            "prompt": value.prompt,
            "difficulty": value.difficulty,
            "max_attempts": value.max_attempts,
            "skill_name": value.skill_name,
            "hints_available": len(hints),
            "hints_requested": value.hints_requested,
            "source_context_count": len(sources),
            "required": value.required,
            "generated": value.generation_sha256 is not None,
            "model_id": value.model_id,
            "attempts": value.attempts,
            "created_at": value.created_at,
        }


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
    source_context_count: int = 0
    grounding_state: str = "general_knowledge"
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
        source_ids = json.loads(value.source_ids_json)
        if not isinstance(objectives, list) or not all(isinstance(item, str) for item in objectives):
            raise ValueError("persisted learning objectives are invalid")
        if not isinstance(memory_ids, list) or not all(isinstance(item, str) for item in memory_ids):
            raise ValueError("persisted learning memory context is invalid")
        if not isinstance(source_ids, list) or not all(isinstance(item, str) for item in source_ids):
            raise ValueError("persisted learning source context is invalid")
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
            "source_context_count": len(source_ids),
            "grounding_state": "source_grounded" if source_ids else "general_knowledge",
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


class LearningSkillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    mastery_bps: int
    confidence_bps: int
    attempts: int
    mistake_count: int
    last_score_bps: int
    last_practiced_at: datetime | None
    next_review_at: datetime | None


class LearningSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    asset_id: UUID
    label: str
    source_sha256: str
    created_at: datetime


class LearningSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    program_id: UUID
    current_lesson_id: UUID | None
    mode: LearningTeachingMode
    status: LearningSessionStatus
    focus: str
    planned_minutes: int
    interruption_count: int
    started_at: datetime
    last_activity_at: datetime
    paused_at: datetime | None
    completed_at: datetime | None


class LearningAnalyticsResponse(BaseModel):
    program_id: UUID
    mastery_bps: int | None
    confidence_bps: int
    weak_topics: list[str]
    due_review_count: int
    current_streak_days: int
    best_streak_days: int
    active_session: LearningSessionResponse | None
    skills: list[LearningSkillResponse]


class LearningStudyPlanItemResponse(BaseModel):
    date: date
    minutes: int
    focus: str
    mode: LearningTeachingMode


class LearningStudyPlanResponse(BaseModel):
    items: list[LearningStudyPlanItemResponse]


class LearningEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action: str
    entity_kind: str
    entity_id: UUID
    metadata_sha256: str
    created_at: datetime


class LearningEventPageResponse(BaseModel):
    items: list[LearningEventResponse]


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
    teaching_mode: LearningTeachingMode = LearningTeachingMode.TEACHER
    preferences: LearningPreferences = Field(default_factory=LearningPreferences)
    status: LearningProgramStatus
    total_lessons: int
    completed_lessons: int
    total_attempts: int
    correct_attempts: int
    current_streak_days: int = 0
    best_streak_days: int = 0
    progress_bps: int
    accuracy_bps: int | None
    lessons: list[LearningLessonResponse]
    review_items: list[LearningReviewItemResponse]
    skills: list[LearningSkillResponse] = Field(default_factory=list)
    sources: list[LearningSourceResponse] = Field(default_factory=list)
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
            "teaching_mode": value.teaching_mode,
            "preferences": json.loads(value.preferences_json),
            "status": value.status,
            "total_lessons": value.total_lessons,
            "completed_lessons": value.completed_lessons,
            "total_attempts": value.total_attempts,
            "correct_attempts": value.correct_attempts,
            "current_streak_days": value.current_streak_days,
            "best_streak_days": value.best_streak_days,
            "progress_bps": value.completed_lessons * 10_000 // value.total_lessons,
            "accuracy_bps": (
                value.correct_attempts * 10_000 // value.total_attempts
                if value.total_attempts
                else None
            ),
            "lessons": value.lessons,
            "review_items": value.review_items,
            "skills": value.skills,
            "sources": value.sources,
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
    adaptive_assessment: bool = True
    rubric_grading: bool = True
    resumable_sessions: bool = True
    document_grounding: bool = True
    audit_history: bool = True
    mixed_language: bool = True
    pronunciation_scoring: bool = False
    pronunciation_status: str = "external_dependency"
    pronunciation_dependencies: list[str] = Field(
        default_factory=lambda: ["pronunciation_scoring_provider"]
    )
