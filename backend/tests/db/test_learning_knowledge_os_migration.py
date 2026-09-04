import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)
from tests.db.test_trading_safety_migration import (
    _upgrade_through_connector_lifecycle,
)


REVISION_FILENAME = "0021_learning_knowledge_os.py"


def _upgrade_through_trading(operations: RecordingOperations) -> None:
    _upgrade_through_connector_lifecycle(operations)
    _load_revision(operations, "0020_trading_safety.py").upgrade()


def _order_independent_signature(table):
    columns, constraints, indexes = _table_signature(table)
    normalized_columns = {
        (*column[:4], column[4].strip("'") if isinstance(column[4], str) else column[4])
        for column in columns
    }
    return normalized_columns, set(constraints), set(indexes)


def test_learning_knowledge_revision_follows_trading_safety():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0021_learning_knowledge_os"
    assert revision.down_revision == "0020_trading_safety"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_current_learning_knowledge_orm_schema():
    operations = RecordingOperations()
    _upgrade_through_trading(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_unique_constraint", "uq_documents_id_owner"),
        ("add_column", "learning_programs.teaching_mode"),
        ("add_column", "learning_programs.preferences_json"),
        ("add_column", "learning_programs.current_streak_days"),
        ("add_column", "learning_programs.best_streak_days"),
        ("add_column", "learning_programs.last_study_date"),
        ("create_check_constraint", "ck_learning_programs_teaching_mode_allowed"),
        ("create_check_constraint", "ck_learning_programs_preferences_json_bounded"),
        ("create_check_constraint", "ck_learning_programs_streak_bounded"),
        ("add_column", "learning_lessons.source_ids_json"),
        ("create_check_constraint", "ck_learning_lessons_source_ids_json_bounded"),
        ("drop_constraint", "ck_learning_activities_kind_allowed"),
        ("create_check_constraint", "ck_learning_activities_kind_allowed"),
        ("add_column", "learning_activities.grading_mode"),
        ("add_column", "learning_activities.skill_name"),
        ("add_column", "learning_activities.hints_json"),
        ("add_column", "learning_activities.rubric_json"),
        ("add_column", "learning_activities.source_ids_json"),
        ("add_column", "learning_activities.hints_requested"),
        ("add_column", "learning_activities.required"),
        ("add_column", "learning_activities.generation_sha256"),
        ("add_column", "learning_activities.model_id"),
        ("create_check_constraint", "ck_learning_activities_grading_mode_allowed"),
        ("create_check_constraint", "ck_learning_activities_skill_name_bounded_nonblank"),
        ("create_check_constraint", "ck_learning_activities_hints_json_bounded"),
        ("create_check_constraint", "ck_learning_activities_rubric_json_bounded"),
        ("create_check_constraint", "ck_learning_activities_source_ids_json_bounded"),
        ("create_check_constraint", "ck_learning_activities_hints_requested_bounded"),
        ("create_check_constraint", "ck_learning_activities_generation_sha256_valid"),
        ("alter_column", "learning_activities.skill_name"),
        ("alter_column", "learning_activities.hints_json"),
        ("alter_column", "learning_activities.rubric_json"),
        ("alter_column", "learning_activities.source_ids_json"),
        ("alter_column", "learning_activities.hints_requested"),
        ("alter_column", "learning_activities.required"),
        ("drop_constraint", "ck_learning_attempts_score_allowed"),
        ("drop_constraint", "ck_learning_attempts_result_consistent"),
        ("add_column", "learning_attempts.mistake_code"),
        ("create_check_constraint", "ck_learning_attempts_score_allowed"),
        ("create_check_constraint", "ck_learning_attempts_result_consistent"),
        ("create_check_constraint", "ck_learning_attempts_mistake_code_safe"),
        ("create_table", "learning_skills"),
        ("create_index", "ix_learning_skills_program_mastery"),
        ("create_table", "learning_sources"),
        ("create_table", "learning_sessions"),
        ("create_index", "ix_learning_sessions_program_started_at"),
        ("create_index", "uq_learning_sessions_open_program"),
        ("create_table", "learning_events"),
        ("create_index", "ix_learning_events_program_created_at"),
    ]
    for table_name in (
        "documents",
        "learning_programs",
        "learning_lessons",
        "learning_activities",
        "learning_attempts",
        "learning_review_items",
        "learning_skills",
        "learning_sources",
        "learning_sessions",
        "learning_events",
    ):
        assert _order_independent_signature(
            operations.metadata.tables[table_name]
        ) == _order_independent_signature(Base.metadata.tables[table_name])


def test_downgrade_restores_the_historical_learning_contract():
    operations = RecordingOperations()
    _upgrade_through_trading(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert [event for event in operations.events if event[0] == "drop_table"] == [
        ("drop_table", "learning_events"),
        ("drop_table", "learning_sessions"),
        ("drop_table", "learning_sources"),
        ("drop_table", "learning_skills"),
    ]
    assert tuple(operations.metadata.tables["learning_activities"].c.keys()) == (
        "id",
        "lesson_id",
        "program_id",
        "owner_id",
        "kind",
        "prompt",
        "expected_answer_sha256",
        "explanation",
        "difficulty",
        "max_attempts",
        "created_at",
    )
    assert "source_ids_json" not in operations.metadata.tables["learning_lessons"].c
    assert "teaching_mode" not in operations.metadata.tables["learning_programs"].c
