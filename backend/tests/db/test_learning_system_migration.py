import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)
from tests.db.test_finance_intelligence_migration import _upgrade_through_marketing


REVISION_FILENAME = "0016_learning_system.py"


def _upgrade_through_finance(operations: RecordingOperations) -> None:
    _upgrade_through_marketing(operations)
    _load_revision(operations, "0015_finance_intelligence.py").upgrade()


def test_learning_revision_follows_finance():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0016_learning_system"
    assert revision.down_revision == "0015_finance_intelligence"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_learning_orm_schema():
    operations = RecordingOperations()
    _upgrade_through_finance(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "learning_programs"),
        ("create_table", "learning_lessons"),
        ("create_table", "learning_activities"),
        ("create_table", "learning_attempts"),
        ("create_table", "learning_review_items"),
        ("create_index", "ix_learning_programs_owner_updated_at"),
        ("create_index", "ix_learning_lessons_program_position"),
        ("create_index", "ix_learning_attempts_program_created_at"),
        ("create_index", "ix_learning_review_items_program_due_at"),
    ]
    expected_columns = {
        "learning_programs": (
            "id", "owner_id", "subject", "goal", "target_language",
            "instruction_language", "start_difficulty", "current_difficulty",
            "target_difficulty", "weekly_minutes", "adaptive_difficulty",
            "status", "total_lessons", "completed_lessons", "total_attempts",
            "correct_attempts", "created_at", "updated_at", "completed_at",
        ),
        "learning_lessons": (
            "id", "program_id", "owner_id", "position", "title",
            "objectives_json", "difficulty", "status", "content",
            "output_sha256", "model_id", "memory_ids_json", "score_bps",
            "created_at", "generated_at", "completed_at",
        ),
        "learning_activities": (
            "id", "lesson_id", "program_id", "owner_id", "kind", "prompt",
            "expected_answer_sha256", "explanation", "difficulty",
            "max_attempts", "created_at",
        ),
        "learning_attempts": (
            "id", "activity_id", "program_id", "owner_id", "answer_sha256",
            "is_correct", "score_bps", "feedback", "created_at",
        ),
        "learning_review_items": tuple(
            Base.metadata.tables["learning_review_items"].c.keys()
        ),
    }
    for table_name, columns in expected_columns.items():
        assert tuple(operations.metadata.tables[table_name].c.keys()) == columns
    activity_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in operations.metadata.tables["learning_activities"].constraints
        if constraint.name and constraint.name.startswith("ck_")
    }
    assert activity_checks["ck_learning_activities_kind_allowed"] == (
        "kind IN ('exercise', 'quiz', 'conversation', 'revision')"
    )
    attempt_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in operations.metadata.tables["learning_attempts"].constraints
        if constraint.name and constraint.name.startswith("ck_")
    }
    assert attempt_checks["ck_learning_attempts_score_allowed"] == (
        "score_bps IN (0, 10000)"
    )


def test_downgrade_removes_learning_relations_in_dependency_order():
    operations = RecordingOperations()
    _upgrade_through_finance(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_learning_review_items_program_due_at"),
        ("drop_index", "ix_learning_attempts_program_created_at"),
        ("drop_index", "ix_learning_lessons_program_position"),
        ("drop_index", "ix_learning_programs_owner_updated_at"),
        ("drop_table", "learning_review_items"),
        ("drop_table", "learning_attempts"),
        ("drop_table", "learning_activities"),
        ("drop_table", "learning_lessons"),
        ("drop_table", "learning_programs"),
    ]
