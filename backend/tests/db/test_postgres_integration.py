import asyncio
from datetime import datetime, timezone
import json
import re
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import app.api.v1.conversations as conversations_module
import app.api.v1.users as users_module
import app.services.conversation_generation as generation_module
from app.ai.catalog import (
    ModelAvailability,
    ModelCatalog,
    RuntimeModel,
)
from app.ai.generation import (
    TextGenerationResult,
    TextGenerationRouter,
    TextGenerationRuntimeUnavailableError,
)
from app.core.security import digest_access_token, generate_access_token
from app.main import app
from app.models import (
    Asset,
    Conversation,
    Memory,
    MemoryCategory,
    Message,
    MessageAsset,
    MessageRole,
    User,
    UserSession,
)
from app.models.message import (
    MAX_MESSAGE_CONTENT_CHARACTERS,
    MessageContentTooLargeError,
)
from app.repositories.conversation import ConversationPagination
from app.repositories.message import MessagePagination
from app.repositories.user import UserRepository
from app.services.conversation import ConversationService
from app.services.generation_admission import GenerationAdmissionController
from app.services.memory import MemoryService
from app.services.message import MessageService
from app.services.user import UserService


pytestmark = pytest.mark.integration

_PROVISIONING_TOKEN = "P" * 43
_PROVISIONING_DIGEST = digest_access_token(_PROVISIONING_TOKEN)
_PROVISIONING_HEADERS = {
    "X-User-Provisioning-Token": _PROVISIONING_TOKEN,
}


class _GenerationDisconnectASGIHarness:
    def __init__(
        self,
        asgi_app: FastAPI,
        conversation_id: UUID,
        authorization: str,
        payload: dict,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        path = (
            f"/api/v1/conversations/{conversation_id}/messages/generate"
        )
        self.scope = {
            "type": "http",
            "app": asgi_app,
            "state": {},
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"authorization", authorization.encode("ascii")),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "root_path": "",
        }
        self._events: asyncio.Queue[dict] = asyncio.Queue()
        self._events.put_nowait(
            {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }
        )
        self.body_consumed = asyncio.Event()
        self.sent: list[dict] = []

    async def receive(self) -> dict:
        message = await self._events.get()
        if (
            message["type"] == "http.request"
            and not message.get("more_body", False)
        ):
            self.body_consumed.set()
        return message

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    def disconnect(self) -> None:
        self._events.put_nowait({"type": "http.disconnect"})


@pytest.fixture(autouse=True)
def configure_user_provisioning(monkeypatch):
    monkeypatch.setattr(
        users_module.settings,
        "USER_PROVISIONING_TOKEN_DIGEST",
        _PROVISIONING_DIGEST,
    )


async def _schema_snapshot(engine: AsyncEngine) -> dict:
    async with engine.connect() as connection:
        return await connection.run_sync(_inspect_schema)


def _inspect_schema(connection) -> dict:
    inspector = sa.inspect(connection)
    return {
        "tables": set(inspector.get_table_names()),
        "conversation_foreign_keys": inspector.get_foreign_keys("conversations"),
        "user_session_foreign_keys": inspector.get_foreign_keys("user_sessions"),
        "message_foreign_keys": inspector.get_foreign_keys("messages"),
        "asset_foreign_keys": inspector.get_foreign_keys("assets"),
        "message_asset_foreign_keys": inspector.get_foreign_keys("message_assets"),
        "document_foreign_keys": inspector.get_foreign_keys("documents"),
        "document_chunk_foreign_keys": inspector.get_foreign_keys("document_chunks"),
        "message_citation_foreign_keys": inspector.get_foreign_keys("message_citations"),
        "memory_setting_foreign_keys": inspector.get_foreign_keys("memory_settings"),
        "memory_foreign_keys": inspector.get_foreign_keys("memories"),
        "tool_execution_foreign_keys": inspector.get_foreign_keys("tool_executions"),
        "workflow_foreign_keys": inspector.get_foreign_keys("workflows"),
        "workflow_step_foreign_keys": inspector.get_foreign_keys("workflow_steps"),
        "connector_foreign_keys": inspector.get_foreign_keys("connectors"),
        "connector_execution_foreign_keys": inspector.get_foreign_keys("connector_executions"),
        "marketing_campaign_foreign_keys": inspector.get_foreign_keys("marketing_campaigns"),
        "marketing_stage_foreign_keys": inspector.get_foreign_keys("marketing_stages"),
        "conversation_indexes": inspector.get_indexes("conversations"),
        "user_session_indexes": inspector.get_indexes("user_sessions"),
        "asset_indexes": inspector.get_indexes("assets"),
        "document_indexes": inspector.get_indexes("documents"),
        "document_chunk_indexes": inspector.get_indexes("document_chunks"),
        "memory_indexes": inspector.get_indexes("memories"),
        "tool_execution_indexes": inspector.get_indexes("tool_executions"),
        "workflow_indexes": inspector.get_indexes("workflows"),
        "workflow_step_indexes": inspector.get_indexes("workflow_steps"),
        "connector_indexes": inspector.get_indexes("connectors"),
        "connector_execution_indexes": inspector.get_indexes("connector_executions"),
        "marketing_campaign_indexes": inspector.get_indexes("marketing_campaigns"),
        "marketing_stage_indexes": inspector.get_indexes("marketing_stages"),
        "conversation_checks": inspector.get_check_constraints("conversations"),
        "user_session_checks": inspector.get_check_constraints("user_sessions"),
        "message_checks": inspector.get_check_constraints("messages"),
        "asset_checks": inspector.get_check_constraints("assets"),
        "message_asset_checks": inspector.get_check_constraints("message_assets"),
        "document_checks": inspector.get_check_constraints("documents"),
        "document_chunk_checks": inspector.get_check_constraints("document_chunks"),
        "message_citation_checks": inspector.get_check_constraints("message_citations"),
        "memory_checks": inspector.get_check_constraints("memories"),
        "tool_execution_checks": inspector.get_check_constraints("tool_executions"),
        "workflow_checks": inspector.get_check_constraints("workflows"),
        "workflow_step_checks": inspector.get_check_constraints("workflow_steps"),
        "connector_checks": inspector.get_check_constraints("connectors"),
        "connector_execution_checks": inspector.get_check_constraints("connector_executions"),
        "marketing_campaign_checks": inspector.get_check_constraints("marketing_campaigns"),
        "marketing_stage_checks": inspector.get_check_constraints("marketing_stages"),
        "user_uniques": inspector.get_unique_constraints("users"),
        "user_session_uniques": inspector.get_unique_constraints("user_sessions"),
        "message_uniques": inspector.get_unique_constraints("messages"),
        "asset_uniques": inspector.get_unique_constraints("assets"),
        "message_asset_uniques": inspector.get_unique_constraints("message_assets"),
        "document_uniques": inspector.get_unique_constraints("documents"),
        "document_chunk_uniques": inspector.get_unique_constraints("document_chunks"),
        "message_citation_uniques": inspector.get_unique_constraints("message_citations"),
        "workflow_step_uniques": inspector.get_unique_constraints("workflow_steps"),
        "workflow_uniques": inspector.get_unique_constraints("workflows"),
        "connector_uniques": inspector.get_unique_constraints("connectors"),
        "marketing_campaign_uniques": inspector.get_unique_constraints("marketing_campaigns"),
        "marketing_stage_uniques": inspector.get_unique_constraints("marketing_stages"),
        "columns": {
            table_name: inspector.get_columns(table_name)
            for table_name in (
                "users",
                "user_sessions",
                "conversations",
                "messages",
                "assets",
                "message_assets",
                "documents",
                "document_chunks",
                "message_citations",
                "memory_settings",
                "memories",
                "tool_executions",
                "workflows",
                "workflow_steps",
                "connectors",
                "connector_executions",
                "marketing_campaigns",
                "marketing_stages",
            )
        },
    }


def _foreign_key(snapshot: dict, table: str, name: str) -> dict:
    foreign_keys = snapshot[f"{table}_foreign_keys"]
    return next(item for item in foreign_keys if item["name"] == name)


def _checks_by_name(items: list[dict]) -> dict[str, str]:
    return {
        item["name"]: " ".join(item["sqltext"].lower().split())
        for item in items
    }


def _columns_by_name(snapshot: dict, table_name: str) -> dict[str, dict]:
    return {
        column["name"]: column
        for column in snapshot["columns"][table_name]
    }


def _assert_required_uuid(column: dict) -> None:
    assert isinstance(column["type"], PostgreSQLUUID)
    assert column["nullable"] is False


def _assert_required_timestamp(column: dict) -> None:
    assert isinstance(column["type"], sa.DateTime)
    assert column["type"].timezone is True
    assert column["nullable"] is False
    assert column["default"] is not None
    assert "now()" in column["default"].lower()


@pytest.mark.asyncio
async def test_migration_creates_exact_expected_postgresql_schema(
    test_database_engine: AsyncEngine,
):
    snapshot = await _schema_snapshot(test_database_engine)

    assert snapshot["tables"] == {
        "alembic_version",
        "users",
        "user_sessions",
        "conversations",
        "messages",
        "assets",
        "message_assets",
        "documents",
        "document_chunks",
        "message_citations",
        "memory_settings",
        "memories",
        "tool_executions",
        "workflows",
        "workflow_steps",
        "connectors",
            "connector_executions",
            "marketing_campaigns",
            "marketing_stages",
            "finance_workspaces",
            "market_watch_items",
            "paper_positions",
            "paper_orders",
            "market_alerts",
            "finance_artifacts",
    }

    owner_fk = _foreign_key(
        snapshot,
        "conversation",
        "fk_conversations_owner_id_users",
    )
    assert owner_fk["constrained_columns"] == ["owner_id"]
    assert owner_fk["referred_table"] == "users"
    assert owner_fk["referred_columns"] == ["id"]
    assert owner_fk["options"]["ondelete"] == "RESTRICT"

    message_fk = _foreign_key(
        snapshot,
        "message",
        "fk_messages_conversation_id_conversations",
    )
    assert message_fk["constrained_columns"] == ["conversation_id"]
    assert message_fk["referred_table"] == "conversations"
    assert message_fk["referred_columns"] == ["id"]
    assert message_fk["options"]["ondelete"] == "CASCADE"

    index = next(
        item
        for item in snapshot["conversation_indexes"]
        if item["name"] == "ix_conversations_owner_updated_at_id"
    )
    assert index["column_names"] == ["owner_id", "updated_at", "id"]
    sorting = index.get("column_sorting", {})
    assert "desc" not in sorting.get("owner_id", ())
    assert "desc" in sorting["updated_at"]
    assert "desc" in sorting["id"]
    archive_index = next(
        item
        for item in snapshot["conversation_indexes"]
        if item["name"] == "ix_conversations_owner_archived_updated_at_id"
    )
    assert archive_index["column_names"] == [
        "owner_id",
        "is_archived",
        "updated_at",
        "id",
    ]

    conversation_checks = _checks_by_name(snapshot["conversation_checks"])
    assert set(conversation_checks) == {
        "ck_conversations_title_non_blank",
        "ck_conversations_next_message_sequence_positive",
    }
    next_sequence_check = conversation_checks[
        "ck_conversations_next_message_sequence_positive"
    ]
    assert "next_message_sequence" in next_sequence_check
    assert ">= 1" in next_sequence_check
    title_check = conversation_checks["ck_conversations_title_non_blank"]
    assert "title is null" in title_check
    assert "char_length" in title_check
    assert "trim(" in title_check or "btrim(" in title_check
    assert "> 0" in title_check

    message_checks = _checks_by_name(snapshot["message_checks"])
    assert set(message_checks) == {
        "ck_messages_content_length_bounded",
        "ck_messages_role_allowed",
        "ck_messages_sequence_number_positive",
    }
    role_check = message_checks["ck_messages_role_allowed"]
    assert "role" in role_check
    assert " in " in f" {role_check} " or " any " in f" {role_check} "
    role_values = set(re.findall(r"'([^']+)'", role_check))
    assert role_values == {"system", "user", "assistant", "tool"}
    sequence_check = message_checks["ck_messages_sequence_number_positive"]
    assert "sequence_number" in sequence_check
    assert ">= 1" in sequence_check
    content_check = message_checks["ck_messages_content_length_bounded"]
    assert "char_length(content)" in content_check
    assert "<= 100000" in content_check

    message_unique = next(
        item
        for item in snapshot["message_uniques"]
        if item["name"] == "uq_messages_conversation_sequence_number"
    )
    assert message_unique["column_names"] == ["conversation_id", "sequence_number"]
    assert len(snapshot["message_uniques"]) == 1

    document_checks = _checks_by_name(snapshot["document_checks"])
    assert set(document_checks) == {
        "ck_documents_chunk_count_nonnegative",
        "ck_documents_character_count_nonnegative",
        "ck_documents_failure_code_safe",
        "ck_documents_processing_token_consistent",
        "ck_documents_status_allowed",
    }
    assert "ingestion_token is not null" in document_checks[
        "ck_documents_processing_token_consistent"
    ]
    status_values = set(
        re.findall(r"'([^']+)'", document_checks["ck_documents_status_allowed"])
    )
    assert status_values == {
        "pending",
        "processing",
        "ready",
        "failed",
        "cancelled",
    }
    assert len(snapshot["document_uniques"]) == 1
    document_unique = snapshot["document_uniques"][0]
    assert document_unique["name"] == "uq_documents_asset_id"
    assert document_unique["column_names"] == ["asset_id"]
    assert {
        item["name"]
        for item in snapshot["document_indexes"]
        if item.get("duplicates_constraint") is None
    } == {
        "ix_documents_owner_status",
        "ix_documents_owner_updated_at",
    }

    chunk_checks = _checks_by_name(snapshot["document_chunk_checks"])
    assert set(chunk_checks) == {
        "ck_document_chunks_content_length_bounded",
        "ck_document_chunks_embedding_bytes_consistent",
        "ck_document_chunks_embedding_dimensions_bounded",
        "ck_document_chunks_embedding_model_safe",
        "ck_document_chunks_embedding_norm_positive",
        "ck_document_chunks_ordinal_positive",
        "ck_document_chunks_page_number_positive",
        "ck_document_chunks_row_range_valid",
        "ck_document_chunks_row_start_positive",
    }
    dimensions_check = chunk_checks[
        "ck_document_chunks_embedding_dimensions_bounded"
    ]
    assert "embedding_dimensions >= 1" in dimensions_check
    assert "embedding_dimensions <= 4096" in dimensions_check
    embedding_bytes_check = chunk_checks[
        "ck_document_chunks_embedding_bytes_consistent"
    ]
    assert "octet_length(embedding)" in embedding_bytes_check
    assert "embedding_dimensions * 4" in embedding_bytes_check
    assert "embedding_model" in chunk_checks[
        "ck_document_chunks_embedding_model_safe"
    ]
    chunk_columns = _columns_by_name(snapshot, "document_chunks")
    assert isinstance(chunk_columns["embedding_model"]["type"], sa.String)
    assert chunk_columns["embedding_model"]["nullable"] is False
    assert isinstance(chunk_columns["embedding_dimensions"]["type"], sa.Integer)
    assert chunk_columns["embedding_dimensions"]["nullable"] is False
    chunk_unique = next(
        item
        for item in snapshot["document_chunk_uniques"]
        if item["name"] == "uq_document_chunks_ordinal"
    )
    assert chunk_unique["column_names"] == ["document_id", "ordinal"]
    assert {
        item["name"]
        for item in snapshot["document_chunk_indexes"]
        if item.get("duplicates_constraint") is None
    } == {"ix_document_chunks_owner_document_ordinal"}

    citation_checks = _checks_by_name(snapshot["message_citation_checks"])
    assert set(citation_checks) == {"ck_message_citations_position_positive"}
    assert {item["name"] for item in snapshot["message_citation_uniques"]} == {
        "uq_message_citations_message_position"
    }
    assert _foreign_key(
        snapshot,
        "document",
        "fk_documents_owner_id_users",
    )["options"]["ondelete"] == "RESTRICT"
    assert _foreign_key(
        snapshot,
        "document_chunk",
        "fk_document_chunks_document_id_documents",
    )["options"]["ondelete"] == "CASCADE"
    assert _foreign_key(
        snapshot,
        "message_citation",
        "fk_message_citations_message_id_messages",
    )["options"]["ondelete"] == "CASCADE"

    memory_checks = _checks_by_name(snapshot["memory_checks"])
    assert set(memory_checks) == {
        "ck_memories_active_content_embedding_consistent",
        "ck_memories_category_allowed",
        "ck_memories_content_bounded_non_blank",
        "ck_memories_provenance_kind_allowed",
    }
    assert "octet_length(embedding) = 1024" in memory_checks[
        "ck_memories_active_content_embedding_consistent"
    ]
    assert "content is null" in memory_checks[
        "ck_memories_active_content_embedding_consistent"
    ]
    assert set(re.findall(r"'([^']+)'", memory_checks["ck_memories_category_allowed"])) == {
        "preference",
        "fact",
        "instruction",
        "project_context",
    }
    assert {
        item["name"]
        for item in snapshot["memory_indexes"]
        if item.get("duplicates_constraint") is None
    } == {"ix_memories_owner_category", "ix_memories_owner_updated_at"}
    assert _foreign_key(
        snapshot,
        "memory_setting",
        "fk_memory_settings_owner_id_users",
    )["options"]["ondelete"] == "RESTRICT"
    assert _foreign_key(
        snapshot,
        "memory",
        "fk_memories_owner_id_users",
    )["options"]["ondelete"] == "RESTRICT"

    memory_settings = _columns_by_name(snapshot, "memory_settings")
    assert set(memory_settings) == {"owner_id", "enabled", "created_at", "updated_at"}
    _assert_required_uuid(memory_settings["owner_id"])
    assert isinstance(memory_settings["enabled"]["type"], sa.Boolean)
    assert memory_settings["enabled"]["nullable"] is False

    memories = _columns_by_name(snapshot, "memories")
    assert set(memories) == {
        "id",
        "owner_id",
        "category",
        "content",
        "embedding",
        "embedding_norm",
        "provenance_kind",
        "created_at",
        "updated_at",
        "deleted_at",
    }
    _assert_required_uuid(memories["id"])
    _assert_required_uuid(memories["owner_id"])
    assert isinstance(memories["content"]["type"], sa.Text)
    assert memories["content"]["nullable"] is True
    assert memories["embedding"]["nullable"] is True

    tool_checks = _checks_by_name(snapshot["tool_execution_checks"])
    assert set(tool_checks) == {
        "ck_tool_executions_arguments_json_bounded",
        "ck_tool_executions_error_code_allowed",
        "ck_tool_executions_initiator_allowed",
        "ck_tool_executions_permission_allowed",
        "ck_tool_executions_result_json_bounded",
        "ck_tool_executions_status_allowed",
        "ck_tool_executions_terminal_state_consistent",
        "ck_tool_executions_tool_name_allowed",
    }
    assert set(re.findall(r"'([^']+)'", tool_checks["ck_tool_executions_tool_name_allowed"])) == {
        "calculator",
        "local_time",
        "document_search",
        "conversation_search",
        "memory_search",
    }
    assert set(
        re.findall(
            r"'([^']+)'",
            tool_checks["ck_tool_executions_initiator_allowed"],
        )
    ) == {"explicit_user", "workflow"}
    assert {
        item["name"]
        for item in snapshot["tool_execution_indexes"]
        if item.get("duplicates_constraint") is None
    } == {
        "ix_tool_executions_owner_started_at",
        "ix_tool_executions_owner_conversation",
    }
    assert _foreign_key(
        snapshot,
        "tool_execution",
        "fk_tool_executions_owner_id_users",
    )["options"]["ondelete"] == "RESTRICT"
    assert _foreign_key(
        snapshot,
        "tool_execution",
        "fk_tool_executions_conversation_id_conversations",
    )["options"]["ondelete"] == "SET NULL"
    tool_executions = _columns_by_name(snapshot, "tool_executions")
    assert set(tool_executions) == {
        "id",
        "owner_id",
        "conversation_id",
        "tool_name",
        "permission",
        "status",
        "initiator",
        "arguments_json",
        "result_json",
        "error_code",
        "started_at",
        "completed_at",
        "duration_ms",
    }
    _assert_required_uuid(tool_executions["id"])
    _assert_required_uuid(tool_executions["owner_id"])
    assert tool_executions["conversation_id"]["nullable"] is True
    _assert_required_timestamp(tool_executions["started_at"])

    workflow_checks = _checks_by_name(snapshot["workflow_checks"])
    assert set(workflow_checks) == {
        "ck_workflows_current_step_position_bounded",
        "ck_workflows_error_code_allowed",
        "ck_workflows_lifecycle_consistent",
        "ck_workflows_name_bounded_non_blank",
        "ck_workflows_result_json_bounded",
        "ck_workflows_status_allowed",
        "ck_workflows_step_count_bounded",
    }
    workflow_step_checks = _checks_by_name(snapshot["workflow_step_checks"])
    assert set(workflow_step_checks) == {
        "ck_workflow_steps_arguments_json_bounded",
        "ck_workflow_steps_error_code_allowed",
        "ck_workflow_steps_lifecycle_consistent",
        "ck_workflow_steps_permission_allowed",
        "ck_workflow_steps_position_bounded",
        "ck_workflow_steps_result_json_bounded",
        "ck_workflow_steps_status_allowed",
        "ck_workflow_steps_tool_name_allowed",
    }
    assert {
        item["name"]
        for item in snapshot["workflow_indexes"]
        if item.get("duplicates_constraint") is None
    } == {"ix_workflows_owner_created_at", "ix_workflows_owner_status"}
    assert {
        item["name"]
        for item in snapshot["workflow_step_indexes"]
        if item.get("duplicates_constraint") is None
    } == {"ix_workflow_steps_owner_workflow_position"}
    assert _foreign_key(
        snapshot,
        "workflow",
        "fk_workflows_owner_id_users",
    )["options"]["ondelete"] == "RESTRICT"
    assert _foreign_key(
        snapshot,
        "workflow_step",
        "fk_workflow_steps_workflow_owner_workflows",
    )["options"]["ondelete"] == "CASCADE"
    assert _foreign_key(
        snapshot,
        "workflow_step",
        "fk_workflow_steps_tool_execution_id_tool_executions",
    )["options"]["ondelete"] == "SET NULL"
    assert {item["name"] for item in snapshot["workflow_step_uniques"]} == {
        "uq_workflow_steps_position"
    }
    assert {item["name"] for item in snapshot["workflow_uniques"]} == {
        "uq_workflows_id_owner"
    }
    workflows = _columns_by_name(snapshot, "workflows")
    assert set(workflows) == {
        "id",
        "owner_id",
        "name",
        "status",
        "step_count",
        "current_step_position",
        "cancel_requested",
        "result_json",
        "error_code",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    }
    _assert_required_uuid(workflows["id"])
    _assert_required_uuid(workflows["owner_id"])
    _assert_required_timestamp(workflows["created_at"])
    _assert_required_timestamp(workflows["updated_at"])
    workflow_steps = _columns_by_name(snapshot, "workflow_steps")
    assert set(workflow_steps) == {
        "id",
        "workflow_id",
        "owner_id",
        "position",
        "tool_name",
        "permission",
        "arguments_json",
        "status",
        "tool_execution_id",
        "result_json",
        "error_code",
        "created_at",
        "started_at",
        "completed_at",
        "duration_ms",
    }
    _assert_required_uuid(workflow_steps["id"])
    _assert_required_uuid(workflow_steps["workflow_id"])
    _assert_required_uuid(workflow_steps["owner_id"])
    _assert_required_timestamp(workflow_steps["created_at"])

    connector_checks = _checks_by_name(snapshot["connector_checks"])
    assert set(connector_checks) == {
        "ck_connectors_auth_kind_allowed",
        "ck_connectors_base_url_bounded",
        "ck_connectors_credential_state_consistent",
        "ck_connectors_health_path_bounded",
        "ck_connectors_health_state_consistent",
        "ck_connectors_health_status_allowed",
        "ck_connectors_kind_allowed",
        "ck_connectors_max_retries_bounded",
        "ck_connectors_name_bounded_nonblank",
        "ck_connectors_path_prefixes_json_bounded",
        "ck_connectors_rate_limit_bounded",
        "ck_connectors_revocation_state_consistent",
        "ck_connectors_scopes_json_bounded",
        "ck_connectors_timeout_seconds_bounded",
    }
    connector_execution_checks = _checks_by_name(
        snapshot["connector_execution_checks"]
    )
    assert set(connector_execution_checks) == {
        "ck_connector_executions_action_allowed",
        "ck_connector_executions_attempts_bounded",
        "ck_connector_executions_duration_nonnegative",
        "ck_connector_executions_error_code_allowed",
        "ck_connector_executions_method_allowed",
        "ck_connector_executions_path_bounded",
        "ck_connector_executions_request_hash_valid",
        "ck_connector_executions_response_bytes_bounded",
        "ck_connector_executions_response_hash_valid",
        "ck_connector_executions_response_status_bounded",
        "ck_connector_executions_status_allowed",
        "ck_connector_executions_terminal_state_consistent",
    }
    assert {
        item["name"]
        for item in snapshot["connector_indexes"]
        if item.get("duplicates_constraint") is None
    } == {"ix_connectors_owner_created_at", "ix_connectors_owner_enabled"}
    assert {
        item["name"]
        for item in snapshot["connector_execution_indexes"]
        if item.get("duplicates_constraint") is None
    } == {
        "ix_connector_executions_owner_started_at",
        "ix_connector_executions_connector_started_at",
    }
    assert {item["name"] for item in snapshot["connector_uniques"]} == {
        "uq_connectors_id_owner"
    }
    assert _foreign_key(
        snapshot,
        "connector",
        "fk_connectors_owner_id_users",
    )["options"]["ondelete"] == "CASCADE"
    connector_owner_fk = _foreign_key(
        snapshot,
        "connector_execution",
        "fk_connector_executions_connector_owner_connectors",
    )
    assert connector_owner_fk["constrained_columns"] == ["connector_id", "owner_id"]
    assert connector_owner_fk["referred_columns"] == ["id", "owner_id"]
    assert connector_owner_fk["options"]["ondelete"] == "CASCADE"
    connectors = _columns_by_name(snapshot, "connectors")
    assert set(connectors) == {
        "id",
        "owner_id",
        "name",
        "kind",
        "base_url",
        "auth_kind",
        "credential_ciphertext",
        "scopes_json",
        "path_prefixes_json",
        "health_path",
        "enabled",
        "timeout_seconds",
        "max_retries",
        "rate_limit_requests_per_minute",
        "health_status",
        "last_health_checked_at",
        "created_at",
        "updated_at",
        "revoked_at",
    }
    _assert_required_uuid(connectors["id"])
    _assert_required_uuid(connectors["owner_id"])
    _assert_required_timestamp(connectors["created_at"])
    _assert_required_timestamp(connectors["updated_at"])
    connector_executions = _columns_by_name(snapshot, "connector_executions")
    assert set(connector_executions) == {
        "id",
        "connector_id",
        "owner_id",
        "action",
        "method",
        "path",
        "status",
        "attempts",
        "response_status_code",
        "request_body_sha256",
        "response_body_sha256",
        "response_bytes",
        "error_code",
        "started_at",
        "completed_at",
        "duration_ms",
    }
    _assert_required_uuid(connector_executions["id"])
    _assert_required_uuid(connector_executions["connector_id"])
    _assert_required_uuid(connector_executions["owner_id"])

    marketing_campaign_checks = _checks_by_name(
        snapshot["marketing_campaign_checks"]
    )
    assert set(marketing_campaign_checks) == {
        "ck_marketing_campaigns_analytics_json_bounded",
        "ck_marketing_campaigns_audience_bounded_nonblank",
        "ck_marketing_campaigns_channels_json_bounded",
        "ck_marketing_campaigns_current_stage_allowed",
        "ck_marketing_campaigns_error_code_allowed",
        "ck_marketing_campaigns_lifecycle_consistent",
        "ck_marketing_campaigns_name_bounded_nonblank",
        "ck_marketing_campaigns_objective_bounded_nonblank",
        "ck_marketing_campaigns_product_bounded_nonblank",
        "ck_marketing_campaigns_publisher_configuration_consistent",
        "ck_marketing_campaigns_source_facts_json_bounded",
        "ck_marketing_campaigns_status_allowed",
    }
    marketing_stage_checks = _checks_by_name(snapshot["marketing_stage_checks"])
    assert set(marketing_stage_checks) == {
        "ck_marketing_stages_error_code_allowed",
        "ck_marketing_stages_lifecycle_consistent",
        "ck_marketing_stages_model_id_bounded",
        "ck_marketing_stages_output_bounded",
        "ck_marketing_stages_output_sha256_valid",
        "ck_marketing_stages_position_bounded",
        "ck_marketing_stages_position_kind_consistent",
        "ck_marketing_stages_status_allowed",
    }
    assert {
        item["name"]
        for item in snapshot["marketing_campaign_indexes"]
        if item.get("duplicates_constraint") is None
    } == {
        "ix_marketing_campaigns_owner_created_at",
        "ix_marketing_campaigns_owner_status",
    }
    assert {
        item["name"]
        for item in snapshot["marketing_stage_indexes"]
        if item.get("duplicates_constraint") is None
    } == {"ix_marketing_stages_owner_campaign_position"}
    assert {item["name"] for item in snapshot["marketing_campaign_uniques"]} == {
        "uq_marketing_campaigns_id_owner"
    }
    assert {item["name"] for item in snapshot["marketing_stage_uniques"]} == {
        "uq_marketing_stages_position"
    }
    publisher_fk = _foreign_key(
        snapshot,
        "marketing_campaign",
        "fk_marketing_campaigns_publisher_owner_connectors",
    )
    assert publisher_fk["constrained_columns"] == [
        "publisher_connector_id",
        "owner_id",
    ]
    assert publisher_fk["referred_columns"] == ["id", "owner_id"]
    campaign_stage_fk = _foreign_key(
        snapshot,
        "marketing_stage",
        "fk_marketing_stages_campaign_owner_campaigns",
    )
    assert campaign_stage_fk["constrained_columns"] == ["campaign_id", "owner_id"]
    assert campaign_stage_fk["referred_columns"] == ["id", "owner_id"]
    assert campaign_stage_fk["options"]["ondelete"] == "CASCADE"
    marketing_campaigns = _columns_by_name(snapshot, "marketing_campaigns")
    assert set(marketing_campaigns) == {
        "id",
        "owner_id",
        "name",
        "objective",
        "product",
        "audience",
        "channels_json",
        "source_facts_json",
        "publisher_connector_id",
        "publish_path",
        "status",
        "current_stage",
        "analytics_json",
        "error_code",
        "created_at",
        "updated_at",
        "started_at",
        "approved_at",
        "published_at",
        "completed_at",
    }
    _assert_required_uuid(marketing_campaigns["id"])
    _assert_required_uuid(marketing_campaigns["owner_id"])
    _assert_required_timestamp(marketing_campaigns["created_at"])
    _assert_required_timestamp(marketing_campaigns["updated_at"])
    marketing_stages = _columns_by_name(snapshot, "marketing_stages")
    assert set(marketing_stages) == {
        "id",
        "campaign_id",
        "owner_id",
        "position",
        "kind",
        "status",
        "output",
        "output_sha256",
        "model_id",
        "connector_execution_id",
        "error_code",
        "created_at",
        "started_at",
        "completed_at",
        "duration_ms",
    }
    _assert_required_uuid(marketing_stages["id"])
    _assert_required_uuid(marketing_stages["campaign_id"])
    _assert_required_uuid(marketing_stages["owner_id"])
    _assert_required_timestamp(marketing_stages["created_at"])

    users = _columns_by_name(snapshot, "users")
    assert set(users) == {
        "id",
        "access_token_digest",
        "created_at",
        "updated_at",
    }
    _assert_required_uuid(users["id"])
    assert isinstance(users["access_token_digest"]["type"], sa.String)
    assert users["access_token_digest"]["type"].length == 64
    assert users["access_token_digest"]["nullable"] is True
    _assert_required_timestamp(users["created_at"])
    _assert_required_timestamp(users["updated_at"])
    user_unique = next(
        item
        for item in snapshot["user_uniques"]
        if item["name"] == "uq_users_access_token_digest"
    )
    assert user_unique["column_names"] == ["access_token_digest"]
    assert len(snapshot["user_uniques"]) == 1

    user_sessions = _columns_by_name(snapshot, "user_sessions")
    assert set(user_sessions) == {
        "id",
        "user_id",
        "access_token_digest",
        "label",
        "created_at",
        "updated_at",
        "revoked_at",
    }
    _assert_required_uuid(user_sessions["id"])
    _assert_required_uuid(user_sessions["user_id"])
    assert isinstance(user_sessions["access_token_digest"]["type"], sa.String)
    assert user_sessions["access_token_digest"]["type"].length == 64
    assert user_sessions["access_token_digest"]["nullable"] is False
    assert isinstance(user_sessions["label"]["type"], sa.String)
    assert user_sessions["label"]["type"].length == 80
    assert user_sessions["label"]["nullable"] is True
    _assert_required_timestamp(user_sessions["created_at"])
    _assert_required_timestamp(user_sessions["updated_at"])
    assert isinstance(user_sessions["revoked_at"]["type"], sa.DateTime)
    assert user_sessions["revoked_at"]["type"].timezone is True
    assert user_sessions["revoked_at"]["nullable"] is True

    user_session_fk = _foreign_key(
        snapshot,
        "user_session",
        "fk_user_sessions_user_id_users",
    )
    assert user_session_fk["constrained_columns"] == ["user_id"]
    assert user_session_fk["referred_table"] == "users"
    assert user_session_fk["referred_columns"] == ["id"]
    assert user_session_fk["options"]["ondelete"] == "CASCADE"
    assert len(snapshot["user_session_uniques"]) == 1
    user_session_unique = snapshot["user_session_uniques"][0]
    assert user_session_unique["name"] == "uq_user_sessions_access_token_digest"
    assert user_session_unique["column_names"] == ["access_token_digest"]
    assert {
        item["name"] for item in snapshot["user_session_indexes"]
        if item.get("duplicates_constraint") is None
    } == {"ix_user_sessions_user_revoked_created_at_id"}
    user_session_checks = _checks_by_name(snapshot["user_session_checks"])
    assert set(user_session_checks) == {
        "ck_user_sessions_access_token_digest_lowercase_hex",
        "ck_user_sessions_label_bounded_nonblank",
        "ck_user_sessions_revoked_at_not_before_created_at",
    }

    conversations = _columns_by_name(snapshot, "conversations")
    assert set(conversations) == {
        "id",
        "owner_id",
        "title",
        "next_message_sequence",
        "is_pinned",
        "is_archived",
        "created_at",
        "updated_at",
    }
    _assert_required_uuid(conversations["id"])
    _assert_required_uuid(conversations["owner_id"])
    assert isinstance(conversations["title"]["type"], sa.String)
    assert conversations["title"]["type"].length == 255
    assert conversations["title"]["nullable"] is True
    assert isinstance(conversations["next_message_sequence"]["type"], sa.BigInteger)
    assert conversations["next_message_sequence"]["nullable"] is False
    assert isinstance(conversations["is_pinned"]["type"], sa.Boolean)
    assert conversations["is_pinned"]["nullable"] is False
    assert isinstance(conversations["is_archived"]["type"], sa.Boolean)
    assert conversations["is_archived"]["nullable"] is False
    _assert_required_timestamp(conversations["created_at"])
    _assert_required_timestamp(conversations["updated_at"])

    messages = _columns_by_name(snapshot, "messages")
    assert set(messages) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "sequence_number",
        "created_at",
        "updated_at",
    }
    _assert_required_uuid(messages["id"])
    _assert_required_uuid(messages["conversation_id"])
    assert isinstance(messages["role"]["type"], sa.String)
    assert messages["role"]["type"].length == 32
    assert messages["role"]["nullable"] is False
    assert isinstance(messages["content"]["type"], sa.Text)
    assert messages["content"]["nullable"] is False
    assert isinstance(messages["sequence_number"]["type"], sa.BigInteger)
    assert messages["sequence_number"]["nullable"] is False
    _assert_required_timestamp(messages["created_at"])
    _assert_required_timestamp(messages["updated_at"])


@pytest.mark.asyncio
async def test_message_content_boundary_is_application_and_database_durable(
    test_database_engine: AsyncEngine,
):
    boundary = "é" * MAX_MESSAGE_CONTENT_CHARACTERS
    oversized = "é" * (MAX_MESSAGE_CONTENT_CHARACTERS + 1)

    async with AsyncSession(
        test_database_engine,
        expire_on_commit=False,
    ) as session:
        user = await UserService(session).create(User())
        owner_id = user.id
        bootstrap = await ConversationService(
            session
        ).create_with_initial_message_for_owner(
            owner_id,
            "Boundary roles",
            MessageRole.USER,
            "initial",
            system_prompt=boundary,
        )
        assert bootstrap is not None
        conversation, initial = bootstrap
        conversation_id = conversation.id
        assert initial.sequence_number == 2

        user_message = await MessageService(session).append_for_owner(
            owner_id,
            conversation_id,
            MessageRole.USER,
            boundary,
        )
        assistant_message = await MessageService(session).append_for_owner(
            owner_id,
            conversation_id,
            MessageRole.ASSISTANT,
            boundary,
        )
        assert user_message is not None
        assert assistant_message is not None
        assert len(user_message.content) == MAX_MESSAGE_CONTENT_CHARACTERS
        assert len(assistant_message.content) == MAX_MESSAGE_CONTENT_CHARACTERS

        conversation_count_before = await session.scalar(
            sa.select(sa.func.count()).select_from(Conversation)
        )
        with pytest.raises(MessageContentTooLargeError):
            await ConversationService(
                session
            ).create_with_initial_message_for_owner(
                owner_id,
                "Rejected system",
                MessageRole.USER,
                "initial",
                system_prompt=oversized,
            )
        with pytest.raises(MessageContentTooLargeError):
            await ConversationService(
                session
            ).create_with_initial_message_for_owner(
                owner_id,
                "Rejected initial",
                MessageRole.USER,
                oversized,
            )
        assert await session.scalar(
            sa.select(sa.func.count()).select_from(Conversation)
        ) == conversation_count_before

        next_sequence_before = conversation.next_message_sequence
        with pytest.raises(MessageContentTooLargeError):
            await MessageService(session).append_for_owner(
                owner_id,
                conversation_id,
                MessageRole.USER,
                oversized,
            )
        await session.refresh(conversation)
        assert conversation.next_message_sequence == next_sequence_before

        direct_conversation = await ConversationService(session).create(
            owner_id,
            "Direct database invariant",
        )
        direct_conversation.next_message_sequence = 2
        direct_message = Message(
            conversation_id=direct_conversation.id,
            role=MessageRole.ASSISTANT,
            content=boundary,
            sequence_number=1,
        )
        session.add(direct_message)
        await session.commit()
        direct_conversation_id = direct_conversation.id
        direct_message_id = direct_message.id

    async with AsyncSession(
        test_database_engine,
        expire_on_commit=False,
    ) as session:
        direct_conversation = await session.get(
            Conversation,
            direct_conversation_id,
        )
        assert direct_conversation is not None
        assert await session.get(Message, direct_message_id) is not None
        direct_conversation.next_message_sequence = 3
        session.add(
            Message(
                conversation_id=direct_conversation_id,
                role=MessageRole.ASSISTANT,
                content=oversized,
                sequence_number=2,
            )
        )
        with pytest.raises(IntegrityError) as captured:
            await session.commit()
        assert "ck_messages_content_length_bounded" in str(captured.value.orig)
        await session.rollback()

    async with AsyncSession(test_database_engine) as session:
        direct_conversation = await session.get(
            Conversation,
            direct_conversation_id,
        )
        assert direct_conversation is not None
        assert direct_conversation.next_message_sequence == 2
        direct_messages = (
            await session.execute(
                sa.select(Message).where(
                    Message.conversation_id == direct_conversation_id
                )
            )
        ).scalars().all()
        assert len(direct_messages) == 1
        assert len(direct_messages[0].content) == (
            MAX_MESSAGE_CONTENT_CHARACTERS
        )


@pytest.mark.asyncio
async def test_service_persistence_and_conversation_delete_cascade(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        user = await UserService(session).create(User())
        conversation = await ConversationService(session).create(
            user.id,
            "PostgreSQL integration",
        )
        message = await MessageService(session).append_for_owner(
            user.id,
            conversation.id,
            MessageRole.USER,
            "integration message",
        )

        assert message is not None
        assert message.sequence_number == 1
        message_id = message.id
        conversation_id = conversation.id
        user_id = user.id

        assert await UserService(session).get_by_id(user_id) is not None
        assert (
            await ConversationService(session).get_for_owner(
                user_id,
                conversation_id,
            )
            is not None
        )
        assert await ConversationService(session).delete_for_owner(
            user_id,
            conversation_id,
        )

    async with AsyncSession(test_database_engine) as verification_session:
        assert await verification_session.get(Conversation, conversation_id) is None
        assert await verification_session.get(Message, message_id) is None
        assert await verification_session.get(User, user_id) is not None


@pytest.mark.asyncio
async def test_conversation_owner_foreign_key_is_enforced(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine) as session:
        with pytest.raises(IntegrityError):
            await ConversationService(session).create(
                uuid4(),
                "orphan conversation",
            )
        assert not session.in_transaction()


@pytest.mark.asyncio
async def test_repository_flush_can_be_rolled_back(
    test_database_engine: AsyncEngine,
):
    user = User()
    user_id = user.id

    async with AsyncSession(test_database_engine) as session:
        await UserRepository(session).create(user)
        user_id = user.id
        await session.rollback()

    async with AsyncSession(test_database_engine) as verification_session:
        assert await verification_session.get(User, user_id) is None


@pytest.mark.asyncio
async def test_conversation_owner_scoped_crud_and_keyset_pagination(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        other_owner = await UserService(session).create(User())
        owner_id = owner.id
        other_owner_id = other_owner.id
        service = ConversationService(session)
        owned = [
            await service.create(owner_id, f"Owned {position}")
            for position in range(3)
        ]
        foreign = await service.create(other_owner_id, "Foreign")
        owned_ids = {conversation.id for conversation in owned}
        foreign_id = foreign.id

        assert await service.get_for_owner(owner_id, foreign_id) is None
        assert (
            await service.rename_for_owner(
                owner_id,
                foreign_id,
                "Must not rename",
            )
            is None
        )
        assert not await service.delete_for_owner(owner_id, foreign_id)

        first_page = await service.list_for_owner(
            owner_id,
            ConversationPagination(limit=2),
        )
        assert len(first_page.items) == 2
        assert first_page.next_cursor is not None
        second_page = await service.list_for_owner(
            owner_id,
            ConversationPagination(limit=2, cursor=first_page.next_cursor),
        )
        assert len(second_page.items) == 1
        assert second_page.next_cursor is None

        listed = first_page.items + second_page.items
        assert {conversation.id for conversation in listed} == owned_ids
        ordering_keys = [
            (conversation.updated_at, conversation.id.int)
            for conversation in listed
        ]
        assert ordering_keys == sorted(ordering_keys, reverse=True)

        target = listed[0]
        target_id = target.id
        previous_updated_at = target.updated_at
        renamed = await service.rename_for_owner(
            owner_id,
            target_id,
            "  Renamed exactly  ",
        )
        assert renamed is not None
        assert renamed.title == "  Renamed exactly  "
        assert renamed.updated_at >= previous_updated_at
        assert await service.get_for_owner(other_owner_id, target_id) is None
        assert await service.delete_for_owner(owner_id, target_id)
        assert await service.get_for_owner(owner_id, target_id) is None
        assert await service.get_for_owner(other_owner_id, foreign_id) is not None


@pytest.mark.asyncio
async def test_conversation_search_matches_owned_titles_and_messages_only(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        other_owner = await UserService(session).create(User())
        service = ConversationService(session)
        title_match, _ = await service.create_with_initial_message_for_owner(
            owner.id,
            "Private GPU roadmap",
            MessageRole.USER,
            "ordinary content",
        )
        message_match, _ = await service.create_with_initial_message_for_owner(
            owner.id,
            "Notes",
            MessageRole.USER,
            "The accelerator codename is Moonstone.",
        )
        literal_match, _ = await service.create_with_initial_message_for_owner(
            owner.id,
            "Literal marker",
            MessageRole.USER,
            "Progress reached 95%_complete.",
        )
        foreign_match, _ = await service.create_with_initial_message_for_owner(
            other_owner.id,
            "Private GPU foreign",
            MessageRole.USER,
            "Moonstone belongs to another owner.",
        )

        title_page = await service.list_for_owner(
            owner.id,
            ConversationPagination(search="gpu roadmap"),
        )
        message_page = await service.list_for_owner(
            owner.id,
            ConversationPagination(search="moonstone"),
        )
        literal_page = await service.list_for_owner(
            owner.id,
            ConversationPagination(search="95%_complete"),
        )

        assert {item.id for item in title_page.items} == {title_match.id}
        assert {item.id for item in message_page.items} == {message_match.id}
        assert {item.id for item in literal_page.items} == {literal_match.id}
        assert foreign_match.id not in {
            item.id for item in title_page.items + message_page.items
        }


@pytest.mark.asyncio
async def test_message_ordered_pagination_and_owner_isolation(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        other_owner = await UserService(session).create(User())
        owner_id = owner.id
        other_owner_id = other_owner.id
        conversation = await ConversationService(session).create(owner_id, "Owned")
        foreign_conversation = await ConversationService(session).create(
            other_owner_id,
            "Foreign",
        )
        conversation_id = conversation.id
        foreign_conversation_id = foreign_conversation.id
        service = MessageService(session)

        for role, content in (
            (MessageRole.SYSTEM, "system"),
            (MessageRole.USER, "question"),
            (MessageRole.ASSISTANT, "answer"),
        ):
            assert (
                await service.append_for_owner(
                    owner_id,
                    conversation_id,
                    role,
                    content,
                )
                is not None
            )

        assert (
            await service.append_for_owner(
                other_owner_id,
                conversation_id,
                MessageRole.USER,
                "must not append",
            )
            is None
        )
        fourth = await service.append_for_owner(
            owner_id,
            conversation_id,
            MessageRole.TOOL,
            "tool",
        )
        assert fourth is not None
        assert fourth.sequence_number == 4

        foreign_message = await service.append_for_owner(
            other_owner_id,
            foreign_conversation_id,
            MessageRole.USER,
            "foreign",
        )
        assert foreign_message is not None

        first_page = await service.list_for_owner(
            owner_id,
            conversation_id,
            MessagePagination(limit=2),
        )
        assert [message.sequence_number for message in first_page.items] == [1, 2]
        assert first_page.next_cursor is not None
        second_page = await service.list_for_owner(
            owner_id,
            conversation_id,
            MessagePagination(limit=2, cursor=first_page.next_cursor),
        )
        messages = first_page.items + second_page.items
        assert [message.sequence_number for message in messages] == [1, 2, 3, 4]
        assert [message.content for message in messages] == [
            "system",
            "question",
            "answer",
            "tool",
        ]
        assert second_page.next_cursor is None

        wrong_owner_page = await service.list_for_owner(
            other_owner_id,
            conversation_id,
        )
        assert wrong_owner_page.items == ()
        assert wrong_owner_page.next_cursor is None
        foreign_conversation_page = await service.list_for_owner(
            owner_id,
            foreign_conversation_id,
        )
        assert foreign_conversation_page.items == ()
        assert foreign_conversation_page.next_cursor is None


@pytest.mark.asyncio
async def test_message_history_uses_sql_character_budgeted_pages(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        other_owner = await UserService(session).create(User())
        owner_id = owner.id
        other_owner_id = other_owner.id
        service = MessageService(session)

        boundary_conversation = await ConversationService(session).create(
            owner_id,
            "Boundary-sized history",
        )
        boundary_contents = (
            "é" * MAX_MESSAGE_CONTENT_CHARACTERS,
            "u" * MAX_MESSAGE_CONTENT_CHARACTERS,
            "a" * MAX_MESSAGE_CONTENT_CHARACTERS,
        )
        for role, content in zip(
            (MessageRole.SYSTEM, MessageRole.USER, MessageRole.ASSISTANT),
            boundary_contents,
            strict=True,
        ):
            appended = await service.append_for_owner(
                owner_id,
                boundary_conversation.id,
                role,
                content,
            )
            assert appended is not None

        cursor = None
        for expected_sequence, expected_content in enumerate(
            boundary_contents,
            start=1,
        ):
            page = await service.list_for_owner(
                owner_id,
                boundary_conversation.id,
                MessagePagination(limit=100, cursor=cursor),
            )
            assert [message.sequence_number for message in page.items] == [
                expected_sequence
            ]
            assert page.items[0].content == expected_content
            if expected_sequence < len(boundary_contents):
                assert page.next_cursor is not None
                assert page.next_cursor.sequence_number == expected_sequence
            else:
                assert page.next_cursor is None
            cursor = page.next_cursor

        mixed_conversation = await ConversationService(session).create(
            owner_id,
            "Mixed-sized history",
        )
        mixed_contents = ("m" * 40_000, "界" * 60_000, "z")
        for content in mixed_contents:
            appended = await service.append_for_owner(
                owner_id,
                mixed_conversation.id,
                MessageRole.USER,
                content,
            )
            assert appended is not None

        mixed_first_page = await service.list_for_owner(
            owner_id,
            mixed_conversation.id,
            MessagePagination(limit=100),
        )
        assert [message.content for message in mixed_first_page.items] == list(
            mixed_contents[:2]
        )
        assert mixed_first_page.next_cursor is not None
        assert mixed_first_page.next_cursor.sequence_number == 2
        mixed_second_page = await service.list_for_owner(
            owner_id,
            mixed_conversation.id,
            MessagePagination(limit=100, cursor=mixed_first_page.next_cursor),
        )
        assert [message.content for message in mixed_second_page.items] == [
            mixed_contents[2]
        ]
        assert mixed_second_page.next_cursor is None

        foreign_conversation = await ConversationService(session).create(
            other_owner_id,
            "Foreign bounded history",
        )
        foreign_message = await service.append_for_owner(
            other_owner_id,
            foreign_conversation.id,
            MessageRole.USER,
            "f" * MAX_MESSAGE_CONTENT_CHARACTERS,
        )
        assert foreign_message is not None
        wrong_owner_page = await service.list_for_owner(
            owner_id,
            foreign_conversation.id,
            MessagePagination(limit=100),
        )
        assert wrong_owner_page.items == ()
        assert wrong_owner_page.next_cursor is None


@pytest.mark.asyncio
async def test_generation_context_snapshot_is_sql_gated_and_owner_scoped(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        other_owner = await UserService(session).create(User())

        async def seed_context(owner_id: UUID, contents: tuple[str, ...]) -> UUID:
            conversation = Conversation(
                owner_id=owner_id,
                title="Generation context snapshot",
                next_message_sequence=len(contents) + 1,
            )
            session.add(conversation)
            await session.flush()
            session.add_all(
                Message(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    content=content,
                    sequence_number=sequence_number,
                )
                for sequence_number, content in enumerate(contents, start=1)
            )
            await session.commit()
            return conversation.id

        valid_count_id = await seed_context(
            owner.id,
            ("a", "界", "🧠") + ("x",) * 97,
        )
        count_overflow_id = await seed_context(owner.id, ("x",) * 101)
        exact_character_content = "a" * 99_997 + "é界🧠"
        exact_characters_id = await seed_context(
            owner.id,
            (exact_character_content,),
        )
        character_overflow_id = await seed_context(
            owner.id,
            (exact_character_content, "x"),
        )
        two_large_messages_id = await seed_context(
            owner.id,
            ("a" * 60_000, "界" * 60_000),
        )
        foreign_id = await seed_context(
            other_owner.id,
            ("f" * MAX_MESSAGE_CONTENT_CHARACTERS,),
        )

    async with AsyncSession(test_database_engine) as session:
        service = MessageService(session)
        valid_count = await service.list_generation_context_for_owner(
            owner.id,
            valid_count_id,
            max_messages=100,
            max_context_characters=100_000,
        )
        assert len(valid_count.messages) == 100
        assert valid_count.candidate_count == 100
        assert valid_count.final_sequence_number == 100
        assert valid_count.oversized is False
        assert [item.content for item in valid_count.messages[:3]] == [
            "a",
            "界",
            "🧠",
        ]
        assert [item.sequence_number for item in valid_count.messages] == list(
            range(1, 101)
        )

        count_overflow = await service.list_generation_context_for_owner(
            owner.id,
            count_overflow_id,
            max_messages=100,
            max_context_characters=100_000,
        )
        assert count_overflow.messages == ()
        assert count_overflow.candidate_count == 101
        assert count_overflow.final_sequence_number == 101
        assert count_overflow.oversized is True

        exact_characters = await service.list_generation_context_for_owner(
            owner.id,
            exact_characters_id,
            max_messages=100,
            max_context_characters=100_000,
        )
        assert [item.content for item in exact_characters.messages] == [
            exact_character_content
        ]
        assert len(exact_characters.messages[0].content) == 100_000
        assert exact_characters.oversized is False

        for oversized_id, expected_count in (
            (character_overflow_id, 2),
            (two_large_messages_id, 2),
        ):
            oversized = await service.list_generation_context_for_owner(
                owner.id,
                oversized_id,
                max_messages=100,
                max_context_characters=100_000,
            )
            assert oversized.messages == ()
            assert oversized.candidate_count == expected_count
            assert oversized.final_sequence_number == expected_count
            assert oversized.oversized is True

        wrong_owner = await service.list_generation_context_for_owner(
            owner.id,
            foreign_id,
            max_messages=100,
            max_context_characters=100_000,
        )
        assert wrong_owner.messages == ()
        assert wrong_owner.candidate_count == 0
        assert wrong_owner.final_sequence_number is None
        assert wrong_owner.oversized is False
        assert not any(
            isinstance(value, Message) for value in session.identity_map.values()
        )


@pytest.mark.asyncio
async def test_concurrent_message_appends_allocate_unique_contiguous_sequences(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        conversation = await ConversationService(session).create(
            owner.id,
            "Concurrent appends",
        )
        owner_id = owner.id
        conversation_id = conversation.id

    async def append_message(position: int) -> int:
        async with AsyncSession(
            test_database_engine,
            expire_on_commit=False,
        ) as concurrent_session:
            message = await MessageService(concurrent_session).append_for_owner(
                owner_id,
                conversation_id,
                MessageRole.USER,
                f"concurrent {position}",
            )
            assert message is not None
            return message.sequence_number

    append_count = 6
    allocated = await asyncio.gather(
        *(append_message(position) for position in range(append_count))
    )
    assert sorted(allocated) == list(range(1, append_count + 1))

    async with AsyncSession(test_database_engine) as verification_session:
        page = await MessageService(verification_session).list_for_owner(
            owner_id,
            conversation_id,
            MessagePagination(limit=append_count),
        )
        assert [message.sequence_number for message in page.items] == list(
            range(1, append_count + 1)
        )
        assert page.next_cursor is None


@pytest.mark.asyncio
async def test_create_with_system_prompt_persists_one_atomic_conversation(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        owner_id = owner.id

        created = await ConversationService(
            session
        ).create_with_initial_message_for_owner(
            owner_id,
            "  Initial title  ",
            MessageRole.USER,
            "  Initial content  ",
            system_prompt="  Exact system content  ",
        )

        assert created is not None
        conversation, message = created
        conversation_id = conversation.id
        message_id = message.id
        assert conversation.owner_id == owner_id
        assert conversation.title == "  Initial title  "
        assert message.conversation_id == conversation_id
        assert message.role is MessageRole.USER
        assert message.content == "  Initial content  "
        assert message.sequence_number == 2

    async with AsyncSession(test_database_engine) as verification_session:
        conversations = (
            (
                await verification_session.execute(
                    sa.select(Conversation).where(Conversation.owner_id == owner_id)
                )
            )
            .scalars()
            .all()
        )
        messages = (
            (
                await verification_session.execute(
                    sa.select(Message).where(
                        Message.conversation_id == conversation_id
                    ).order_by(Message.sequence_number)
                )
            )
            .scalars()
            .all()
        )

        assert len(conversations) == 1
        assert conversations[0].id == conversation_id
        assert conversations[0].next_message_sequence == 3
        assert len(messages) == 2
        assert messages[0].conversation_id == conversation_id
        assert messages[0].role is MessageRole.SYSTEM
        assert messages[0].content == "  Exact system content  "
        assert messages[0].sequence_number == 1
        assert messages[1].id == message_id
        assert messages[1].conversation_id == conversation_id
        assert messages[1].role is MessageRole.USER
        assert messages[1].content == "  Initial content  "
        assert messages[1].sequence_number == 2


@pytest.mark.asyncio
async def test_create_with_initial_message_failure_rolls_back_all_persistence(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        owner_id = owner.id

        with pytest.raises(IntegrityError):
            await ConversationService(
                session
            ).create_with_initial_message_for_owner(
                owner_id,
                "Must roll back",
                None,  # type: ignore[arg-type]
                "Initial content must also roll back",
                system_prompt="System content must also roll back",
            )

        assert not session.in_transaction()

    async with AsyncSession(test_database_engine) as verification_session:
        conversations = (
            (
                await verification_session.execute(
                    sa.select(Conversation).where(Conversation.owner_id == owner_id)
                )
            )
            .scalars()
            .all()
        )
        messages = (
            (
                await verification_session.execute(
                    sa.select(Message)
                    .join(
                        Conversation,
                        Conversation.id == Message.conversation_id,
                    )
                    .where(Conversation.owner_id == owner_id)
                )
            )
            .scalars()
            .all()
        )

        assert conversations == []
        assert messages == []
        assert await verification_session.get(User, owner_id) is not None


@pytest.mark.asyncio
async def test_user_access_credential_persistence_lookup_and_uniqueness(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(
        test_database_engine,
        expire_on_commit=False,
    ) as session:
        service = UserService(session)
        user, access_token = await service.provision_with_access_token()
        user_id = user.id
        expected_digest = digest_access_token(access_token)

        assert user.access_token_digest is None
        assert user.authenticated_session_id is not None
        assert user.authenticated_session_digest == expected_digest
        assert (
            await service.get_by_access_token_digest(expected_digest)
        ).id == user_id
        assert (
            await service.get_by_access_token_digest(
                digest_access_token(generate_access_token())
            )
            is None
        )

    async with AsyncSession(test_database_engine) as verification_session:
        stored = await verification_session.get(User, user_id)
        assert stored is not None
        assert stored.access_token_digest is None
        stored_session = await verification_session.scalar(
            sa.select(UserSession).where(UserSession.user_id == user_id)
        )
        assert stored_session is not None
        assert stored_session.access_token_digest == expected_digest
        assert stored_session.access_token_digest != access_token
        assert stored_session.revoked_at is None

        with pytest.raises(IntegrityError):
            await UserRepository(verification_session).create_access_session(
                UserSession(
                    user_id=user_id,
                    access_token_digest=expected_digest,
                )
            )
        await verification_session.rollback()

        assert not verification_session.in_transaction()
        original = await UserService(
            verification_session
        ).get_by_access_token_digest(expected_digest)
        assert original is not None
        assert original.id == user_id


@pytest.mark.asyncio
async def test_user_access_credential_flush_can_be_rolled_back(
    test_database_engine: AsyncEngine,
):
    access_token = generate_access_token()
    access_token_digest = digest_access_token(access_token)

    async with AsyncSession(
        test_database_engine,
        expire_on_commit=False,
    ) as session:
        user = await UserRepository(session).create(User(access_token_digest=None))
        access_session = await UserRepository(session).create_access_session(
            UserSession(
                user_id=user.id,
                access_token_digest=access_token_digest,
            )
        )
        user_id = user.id
        session_id = access_session.id
        await session.rollback()

    async with AsyncSession(test_database_engine) as verification_session:
        assert await verification_session.get(User, user_id) is None
        assert await verification_session.get(UserSession, session_id) is None
        assert (
            await UserService(
                verification_session
            ).get_by_access_token_digest(access_token_digest)
            is None
        )


@pytest.mark.asyncio
async def test_authenticated_user_lookup_is_self_only_and_read_only(
    test_database_engine: AsyncEngine,
):
    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    app.state.db_session_factory = session_factory

    async def user_state() -> tuple[tuple[tuple, ...], tuple[tuple, ...]]:
        async with AsyncSession(test_database_engine) as verification_session:
            users = await verification_session.execute(
                sa.select(
                    User.id,
                    User.access_token_digest,
                    User.created_at,
                    User.updated_at,
                ).order_by(User.id)
            )
            sessions = await verification_session.execute(
                sa.select(
                    UserSession.id,
                    UserSession.user_id,
                    UserSession.access_token_digest,
                    UserSession.label,
                    UserSession.created_at,
                    UserSession.updated_at,
                    UserSession.revoked_at,
                ).order_by(UserSession.id)
            )
            return (
                tuple(tuple(row) for row in users.all()),
                tuple(tuple(row) for row in sessions.all()),
            )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            owner_response = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            foreign_response = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            assert owner_response.status_code == 201
            assert foreign_response.status_code == 201
            owner = owner_response.json()
            foreign = foreign_response.json()
            owner_headers = {
                "Authorization": f"Bearer {owner['access_token']}"
            }

            state_before = await user_state()

            unauthenticated = await client.get(
                f"/api/v1/users/{owner['id']}"
            )
            provisioning_only = await client.get(
                f"/api/v1/users/{owner['id']}",
                headers=_PROVISIONING_HEADERS,
            )
            self_lookup = await client.get(
                f"/api/v1/users/{owner['id']}",
                headers=owner_headers,
            )
            foreign_lookup = await client.get(
                f"/api/v1/users/{foreign['id']}",
                headers=owner_headers,
            )
            nonexistent_lookup = await client.get(
                f"/api/v1/users/{uuid4()}",
                headers=owner_headers,
            )

            for rejected in (unauthenticated, provisioning_only):
                assert rejected.status_code == 401
                assert rejected.headers["WWW-Authenticate"] == "Bearer"
                assert rejected.json()["error"] == {
                    "code": "HTTP_ERROR",
                    "message": "Invalid authentication credentials",
                }

            assert self_lookup.status_code == 200
            assert self_lookup.json() == {
                "id": owner["id"],
                "created_at": owner["created_at"],
                "updated_at": owner["updated_at"],
            }
            assert foreign_lookup.status_code == 404
            assert nonexistent_lookup.status_code == 404
            assert foreign_lookup.json()["error"] == {
                "code": "HTTP_ERROR",
                "message": "User not found",
            }
            assert nonexistent_lookup.json()["error"] == (
                foreign_lookup.json()["error"]
            )

            response_text = "".join(
                response.text
                for response in (
                    unauthenticated,
                    provisioning_only,
                    self_lookup,
                    foreign_lookup,
                    nonexistent_lookup,
                )
            )
            assert owner["access_token"] not in response_text
            assert foreign["access_token"] not in response_text
            assert _PROVISIONING_TOKEN not in response_text
            assert _PROVISIONING_DIGEST not in response_text
            assert "access_token_digest" not in response_text

            state_after = await user_state()
            assert state_after == state_before
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory


@pytest.mark.asyncio
async def test_authenticated_access_token_rotation_is_atomic_and_preserves_owner_data(
    test_database_engine: AsyncEngine,
    monkeypatch,
):
    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    app.state.db_session_factory = session_factory

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:

            async with AsyncSession(
                test_database_engine
            ) as verification_session:
                user_count_before = await verification_session.scalar(
                    sa.select(sa.func.count()).select_from(User)
                )

            unauthorized = await client.post("/api/v1/users")
            assert unauthorized.status_code == 403
            assert unauthorized.json()["error"] == {
                "code": "HTTP_ERROR",
                "message": "User provisioning is not authorized",
            }

            async with AsyncSession(
                test_database_engine
            ) as verification_session:
                user_count_after = await verification_session.scalar(
                    sa.select(sa.func.count()).select_from(User)
                )
            assert user_count_after == user_count_before
            provisioned = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            assert provisioned.status_code == 201
            provisioned_payload = provisioned.json()
            user_id = UUID(provisioned_payload["id"])
            original_token = provisioned_payload["access_token"]

            created = await client.post(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {original_token}"},
                json={
                    "title": "Credential rotation ownership",
                    "initial_message": "Persist across credential rotation",
                },
            )
            assert created.status_code == 201
            created_payload = created.json()
            conversation_id = UUID(created_payload["id"])
            message_id = UUID(created_payload["initial_message"]["id"])

            rotated = await client.post(
                "/api/v1/users/me/access-token/rotate",
                headers={"Authorization": f"Bearer {original_token}"},
                json={},
            )
            assert rotated.status_code == 200
            first_replacement = rotated.json()["access_token"]
            assert rotated.json() == {
                "access_token": first_replacement,
                "token_type": "bearer",
            }
            assert rotated.headers["Cache-Control"] == "no-store"
            assert rotated.text.count(first_replacement) == 1

            rejected_original = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {original_token}"},
            )
            assert rejected_original.status_code == 401
            assert rejected_original.json()["error"]["message"] == (
                "Invalid authentication credentials"
            )

            resolved_replacement = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {first_replacement}"},
            )
            assert resolved_replacement.status_code == 200
            assert UUID(resolved_replacement.json()["id"]) == user_id

            original_rotate = UserService.rotate_access_token
            both_authenticated = asyncio.Event()
            arrivals = 0

            async def coordinated_rotate(
                service,
                authenticated_user_id,
                authenticated_session_id,
                expected_access_token_digest,
            ):
                nonlocal arrivals
                arrivals += 1
                if arrivals == 2:
                    both_authenticated.set()
                await asyncio.wait_for(both_authenticated.wait(), timeout=5)
                return await original_rotate(
                    service,
                    authenticated_user_id,
                    authenticated_session_id,
                    expected_access_token_digest,
                )

            monkeypatch.setattr(
                UserService,
                "rotate_access_token",
                coordinated_rotate,
            )
            concurrent_headers = {
                "Authorization": f"Bearer {first_replacement}"
            }
            concurrent = await asyncio.gather(
                client.post(
                    "/api/v1/users/me/access-token/rotate",
                    headers=concurrent_headers,
                ),
                client.post(
                    "/api/v1/users/me/access-token/rotate",
                    headers=concurrent_headers,
                ),
            )

            assert sorted(response.status_code for response in concurrent) == [
                200,
                409,
            ]
            winner = next(
                response for response in concurrent if response.status_code == 200
            )
            loser = next(
                response for response in concurrent if response.status_code == 409
            )
            winning_token = winner.json()["access_token"]
            assert winner.json() == {
                "access_token": winning_token,
                "token_type": "bearer",
            }
            assert winner.headers["Cache-Control"] == "no-store"
            assert loser.json()["error"] == {
                "code": "HTTP_ERROR",
                "message": "Access token rotation conflict",
            }
            assert "access_token" not in loser.text
            assert first_replacement not in loser.text

            rejected_replaced = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {first_replacement}"},
            )
            assert rejected_replaced.status_code == 401

            resolved_winner = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {winning_token}"},
            )
            assert resolved_winner.status_code == 200
            assert UUID(resolved_winner.json()["id"]) == user_id

            owned_conversation = await client.get(
                f"/api/v1/conversations/{conversation_id}",
                headers={"Authorization": f"Bearer {winning_token}"},
            )
            assert owned_conversation.status_code == 200
            assert UUID(owned_conversation.json()["id"]) == conversation_id

            owned_messages = await client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers={"Authorization": f"Bearer {winning_token}"},
            )
            assert owned_messages.status_code == 200
            assert [UUID(item["id"]) for item in owned_messages.json()["items"]] == [
                message_id
            ]
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory

    async with AsyncSession(test_database_engine) as verification_session:
        stored_user = await verification_session.get(User, user_id)
        stored_conversation = await verification_session.get(
            Conversation,
            conversation_id,
        )
        stored_message = await verification_session.get(Message, message_id)

        assert stored_user is not None
        assert stored_user.id == user_id
        assert stored_user.access_token_digest is None
        stored_session = await verification_session.scalar(
            sa.select(UserSession).where(UserSession.user_id == user_id)
        )
        assert stored_session is not None
        assert stored_session.access_token_digest == digest_access_token(winning_token)
        assert stored_session.access_token_digest not in {
            original_token,
            first_replacement,
            winning_token,
        }
        assert stored_conversation is not None
        assert stored_conversation.owner_id == user_id
        assert stored_message is not None
        assert stored_message.conversation_id == conversation_id
        assert stored_message.role is MessageRole.USER
        assert stored_message.content == "Persist across credential rotation"
        assert stored_message.sequence_number == 1


@pytest.mark.asyncio
async def test_owner_device_sessions_are_independent_bounded_and_revocable(
    test_database_engine: AsyncEngine,
):
    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    app.state.db_session_factory = session_factory

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            provisioned = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            assert provisioned.status_code == 201
            owner = provisioned.json()
            user_id = UUID(owner["id"])
            original_token = owner["access_token"]
            original_headers = {
                "Authorization": f"Bearer {original_token}"
            }

            created = await client.post(
                "/api/v1/users/me/sessions",
                headers=original_headers,
                json={"label": "Phone"},
            )
            assert created.status_code == 201
            assert created.headers["Cache-Control"] == "no-store"
            created_payload = created.json()
            phone_token = created_payload["access_token"]
            phone_session_id = UUID(created_payload["session"]["id"])
            assert created.text.count(phone_token) == 1
            assert "access_token_digest" not in created.text

            listed_from_original = await client.get(
                "/api/v1/users/me/sessions",
                headers=original_headers,
            )
            assert listed_from_original.status_code == 200
            assert listed_from_original.headers["Cache-Control"] == (
                "private, no-store"
            )
            original_items = listed_from_original.json()["items"]
            assert len(original_items) == 2
            original_session = next(
                item for item in original_items if item["is_current"]
            )
            assert UUID(original_session["id"]) != phone_session_id
            assert phone_token not in listed_from_original.text
            assert original_token not in listed_from_original.text

            phone_headers = {"Authorization": f"Bearer {phone_token}"}
            listed_from_phone = await client.get(
                "/api/v1/users/me/sessions",
                headers=phone_headers,
            )
            assert listed_from_phone.status_code == 200
            assert next(
                item
                for item in listed_from_phone.json()["items"]
                if item["is_current"]
            )["id"] == str(phone_session_id)

            renamed = await client.patch(
                "/api/v1/users/me/sessions/current",
                headers=phone_headers,
                json={"label": "Owner phone"},
            )
            assert renamed.status_code == 200
            assert renamed.json()["id"] == str(phone_session_id)
            assert renamed.json()["label"] == "Owner phone"
            assert renamed.json()["is_current"] is True

            assert (
                await client.get("/api/v1/users/me", headers=original_headers)
            ).status_code == 200
            assert (
                await client.get("/api/v1/users/me", headers=phone_headers)
            ).status_code == 200

            revoked_phone = await client.delete(
                f"/api/v1/users/me/sessions/{phone_session_id}",
                headers=original_headers,
            )
            assert revoked_phone.status_code == 204
            assert (
                await client.get("/api/v1/users/me", headers=phone_headers)
            ).status_code == 401
            assert (
                await client.get("/api/v1/users/me", headers=original_headers)
            ).status_code == 200

            revoked_current = await client.delete(
                "/api/v1/users/me/sessions/current",
                headers=original_headers,
            )
            assert revoked_current.status_code == 204
            assert (
                await client.get("/api/v1/users/me", headers=original_headers)
            ).status_code == 401
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory

    async with AsyncSession(test_database_engine) as verification_session:
        stored_sessions = tuple(
            (
                await verification_session.execute(
                    sa.select(UserSession).where(UserSession.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(stored_sessions) == 2
        assert all(item.revoked_at is not None for item in stored_sessions)
        assert {item.access_token_digest for item in stored_sessions} == {
            digest_access_token(original_token),
            digest_access_token(phone_token),
        }
        assert all(
            item.access_token_digest not in {original_token, phone_token}
            for item in stored_sessions
        )


@pytest.mark.asyncio
async def test_authenticated_conversation_creation_uses_current_user_and_sequence(
    test_database_engine: AsyncEngine,
):
    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    app.state.db_session_factory = session_factory

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            provisioned = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            assert provisioned.status_code == 201
            user_payload = provisioned.json()
            user_id = UUID(user_payload["id"])
            access_token = user_payload["access_token"]

            created = await client.post(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "title": "  API integration  ",
                    "initial_message": "  Exact API content  ",
                },
            )
            assert created.status_code == 201
            created_payload = created.json()

            appended = await client.post(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"content": "  Exact API follow-up  "},
            )
            assert appended.status_code == 201
            appended_payload = appended.json()

            for invalid_content in ("", " \t\r\n"):
                rejected_creation = await client.post(
                    "/api/v1/conversations",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"initial_message": invalid_content},
                )
                assert rejected_creation.status_code == 422
                assert (
                    rejected_creation.json()["error"]["code"]
                    == "VALIDATION_ERROR"
                )

                rejected_append = await client.post(
                    f"/api/v1/conversations/{created_payload['id']}/messages",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"content": invalid_content},
                )
                assert rejected_append.status_code == 422
                assert (
                    rejected_append.json()["error"]["code"]
                    == "VALIDATION_ERROR"
                )

            additional_owned_payloads = []
            for position in range(2):
                additional_owned = await client.post(
                    "/api/v1/conversations",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "title": f"Owned API conversation {position}",
                        "initial_message": f"Owned initial message {position}",
                    },
                )
                assert additional_owned.status_code == 201
                additional_owned_payloads.append(additional_owned.json())

            first_message_page = await client.get(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"limit": 1},
            )
            assert first_message_page.status_code == 200
            first_message_page_payload = first_message_page.json()

            second_message_page = await client.get(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "limit": 1,
                    "cursor": first_message_page_payload["next_cursor"],
                },
            )
            assert second_message_page.status_code == 200
            second_message_page_payload = second_message_page.json()

            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as setup_session:
                empty_conversation = await ConversationService(
                    setup_session
                ).create(user_id, "Empty API conversation")

            empty_message_page = await client.get(
                f"/api/v1/conversations/{empty_conversation.id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert empty_message_page.status_code == 200
            empty_message_page_payload = empty_message_page.json()

            missing_message_page = await client.get(
                f"/api/v1/conversations/{uuid4()}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert missing_message_page.status_code == 200
            missing_message_page_payload = missing_message_page.json()

            second_provisioned = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            assert second_provisioned.status_code == 201
            second_user_payload = second_provisioned.json()
            foreign_conversation = await client.post(
                "/api/v1/conversations",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
                json={
                    "title": "Foreign API conversation",
                    "initial_message": "Foreign initial message",
                },
            )
            assert foreign_conversation.status_code == 201
            foreign_conversation_payload = foreign_conversation.json()

            empty_user = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            assert empty_user.status_code == 201
            empty_user_payload = empty_user.json()
            spoofed_owner = await client.post(
                "/api/v1/conversations",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
                json={
                    "owner_id": str(user_id),
                    "title": "Must not persist",
                    "initial_message": "Must not persist",
                },
            )
            assert spoofed_owner.status_code == 422

            foreign_append = await client.post(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
                json={"content": "Must not persist"},
            )
            assert foreign_append.status_code == 404
            assert foreign_append.json()["error"] == {
                "code": "HTTP_ERROR",
                "message": "Conversation not found",
            }

            foreign_message_page = await client.get(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
            )
            assert foreign_message_page.status_code == 200
            foreign_message_page_payload = foreign_message_page.json()

            conversation_id = UUID(created_payload["id"])
            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as setup_session:
                before_get_conversation = (
                    await setup_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == conversation_id)
                    )
                ).one()
                before_get_messages = [
                    tuple(row)
                    for row in (
                        await setup_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(Message.conversation_id == conversation_id)
                            .order_by(Message.sequence_number)
                        )
                    ).all()
                ]

            owned_conversation_response = await client.get(
                f"/api/v1/conversations/{conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert owned_conversation_response.status_code == 200
            owned_conversation_payload = owned_conversation_response.json()
            assert set(owned_conversation_payload) == {
                "id",
                "title",
                "is_pinned",
                "is_archived",
                "created_at",
                "updated_at",
            }
            assert owned_conversation_payload["id"] == str(conversation_id)
            assert owned_conversation_payload["title"] == "  API integration  "
            owned_response_text = owned_conversation_response.text.lower()
            assert "owner_id" not in owned_response_text
            assert "next_message_sequence" not in owned_response_text
            assert "messages" not in owned_response_text
            assert "credential" not in owned_response_text
            assert "digest" not in owned_response_text

            foreign_get_response = await client.get(
                f"/api/v1/conversations/{conversation_id}",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
            )
            missing_get_response = await client.get(
                f"/api/v1/conversations/{uuid4()}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            expected_not_found = {
                "code": "HTTP_ERROR",
                "message": "Conversation not found",
            }
            assert foreign_get_response.status_code == 404
            assert missing_get_response.status_code == 404
            assert foreign_get_response.json()["error"] == expected_not_found
            assert missing_get_response.json()["error"] == expected_not_found

            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as verification_session:
                after_get_conversation = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == conversation_id)
                    )
                ).one()
                after_get_messages = [
                    tuple(row)
                    for row in (
                        await verification_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(Message.conversation_id == conversation_id)
                            .order_by(Message.sequence_number)
                        )
                    ).all()
                ]

            assert tuple(after_get_conversation) == tuple(before_get_conversation)
            assert after_get_messages == before_get_messages

            rename_title = "  Renamed through API  "
            rename_response = await client.patch(
                f"/api/v1/conversations/{conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"title": rename_title},
            )
            assert rename_response.status_code == 200
            rename_payload = rename_response.json()
            assert set(rename_payload) == {
                "id",
                "title",
                "is_pinned",
                "is_archived",
                "created_at",
                "updated_at",
            }
            assert rename_payload["id"] == str(conversation_id)
            assert rename_payload["title"] == rename_title
            rename_response_text = rename_response.text.lower()
            assert "owner_id" not in rename_response_text
            assert "next_message_sequence" not in rename_response_text
            assert "messages" not in rename_response_text
            assert "credential" not in rename_response_text
            assert "digest" not in rename_response_text

            renamed_get_response = await client.get(
                f"/api/v1/conversations/{conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert renamed_get_response.status_code == 200
            assert renamed_get_response.json()["title"] == rename_title

            renamed_message_history = await client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert renamed_message_history.status_code == 200
            renamed_message_history_payload = renamed_message_history.json()
            assert [
                item["sequence_number"]
                for item in renamed_message_history_payload["items"]
            ] == [1, 2]
            assert renamed_message_history_payload["next_cursor"] is None

            foreign_conversation_id = UUID(foreign_conversation_payload["id"])
            affected_conversation_ids = [
                conversation_id,
                foreign_conversation_id,
            ]
            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as verification_session:
                owner_after_rename = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.title,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == conversation_id)
                    )
                ).one()
                foreign_before_failed_renames = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.title,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == foreign_conversation_id)
                    )
                ).one()
                messages_before_failed_renames = [
                    tuple(row)
                    for row in (
                        await verification_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(
                                Message.conversation_id.in_(
                                    affected_conversation_ids
                                )
                            )
                            .order_by(
                                Message.conversation_id,
                                Message.sequence_number,
                            )
                        )
                    ).all()
                ]

            assert owner_after_rename.owner_id == before_get_conversation.owner_id
            assert owner_after_rename.title == rename_title
            assert (
                owner_after_rename.next_message_sequence
                == before_get_conversation.next_message_sequence
            )
            assert (
                owner_after_rename.updated_at
                >= before_get_conversation.updated_at
            )
            response_updated_at = datetime.fromisoformat(
                rename_payload["updated_at"].replace("Z", "+00:00")
            )
            assert response_updated_at == owner_after_rename.updated_at
            assert [
                row
                for row in messages_before_failed_renames
                if row[1] == conversation_id
            ] == before_get_messages

            foreign_rename_response = await client.patch(
                f"/api/v1/conversations/{foreign_conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"title": "Must not rename foreign conversation"},
            )
            missing_rename_response = await client.patch(
                f"/api/v1/conversations/{uuid4()}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"title": "Must not rename missing conversation"},
            )
            expected_rename_not_found = {
                "code": "HTTP_ERROR",
                "message": "Conversation not found",
            }
            assert foreign_rename_response.status_code == 404
            assert missing_rename_response.status_code == 404
            assert (
                foreign_rename_response.json()["error"]
                == expected_rename_not_found
            )
            assert (
                missing_rename_response.json()["error"]
                == expected_rename_not_found
            )

            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as verification_session:
                owner_after_failed_renames = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.title,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == conversation_id)
                    )
                ).one()
                foreign_after_failed_renames = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.title,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == foreign_conversation_id)
                    )
                ).one()
                messages_after_failed_renames = [
                    tuple(row)
                    for row in (
                        await verification_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(
                                Message.conversation_id.in_(
                                    affected_conversation_ids
                                )
                            )
                            .order_by(
                                Message.conversation_id,
                                Message.sequence_number,
                            )
                        )
                    ).all()
                ]

            assert tuple(owner_after_failed_renames) == tuple(owner_after_rename)
            assert tuple(foreign_after_failed_renames) == tuple(
                foreign_before_failed_renames
            )
            assert (
                messages_after_failed_renames
                == messages_before_failed_renames
            )

            owned_conversation_ids = [
                UUID(created_payload["id"]),
                *(UUID(payload["id"]) for payload in additional_owned_payloads),
                empty_conversation.id,
            ]
            newer_ids = owned_conversation_ids[:2]
            older_ids = owned_conversation_ids[2:]
            newer_timestamp = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            older_timestamp = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as setup_session:
                await setup_session.execute(
                    sa.update(Conversation)
                    .where(Conversation.id.in_(newer_ids))
                    .values(updated_at=newer_timestamp)
                )
                await setup_session.execute(
                    sa.update(Conversation)
                    .where(Conversation.id.in_(older_ids))
                    .values(updated_at=older_timestamp)
                )
                await setup_session.commit()
                before_listing_rows = (
                    await setup_session.execute(
                        sa.select(
                            Conversation.id,
                            Conversation.owner_id,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id.in_(owned_conversation_ids))
                    )
                ).all()
                before_listing = {
                    row.id: (
                        row.owner_id,
                        row.updated_at,
                        row.next_message_sequence,
                    )
                    for row in before_listing_rows
                }

            first_conversation_page = await client.get(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"limit": 2},
            )
            assert first_conversation_page.status_code == 200
            first_conversation_page_payload = first_conversation_page.json()
            returned_conversation_cursor = first_conversation_page_payload[
                "next_cursor"
            ]
            assert returned_conversation_cursor is not None

            second_conversation_page = await client.get(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "limit": 2,
                    "cursor_updated_at": returned_conversation_cursor[
                        "updated_at"
                    ],
                    "cursor_id": returned_conversation_cursor["id"],
                },
            )
            assert second_conversation_page.status_code == 200
            second_conversation_page_payload = second_conversation_page.json()

            foreign_conversation_page = await client.get(
                "/api/v1/conversations",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
            )
            assert foreign_conversation_page.status_code == 200
            foreign_conversation_page_payload = foreign_conversation_page.json()

            empty_conversation_page = await client.get(
                "/api/v1/conversations",
                headers={
                    "Authorization": f"Bearer {empty_user_payload['access_token']}"
                },
            )
            assert empty_conversation_page.status_code == 200
            empty_conversation_page_payload = empty_conversation_page.json()


            deletion_target = await client.post(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "title": "Deletion target",
                    "initial_message": "Deletion target initial message",
                },
            )
            assert deletion_target.status_code == 201
            deletion_target_payload = deletion_target.json()
            deletion_target_id = UUID(deletion_target_payload["id"])

            deletion_target_append = await client.post(
                f"/api/v1/conversations/{deletion_target_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"content": "Deletion target follow-up message"},
            )
            assert deletion_target_append.status_code == 201
            deletion_target_append_payload = deletion_target_append.json()

            deletion_snapshot_ids = [
                *owned_conversation_ids,
                foreign_conversation_id,
                deletion_target_id,
            ]

            async def deletion_state():
                async with AsyncSession(
                    test_database_engine,
                    expire_on_commit=False,
                ) as verification_session:
                    owner_user = tuple(
                        (
                            await verification_session.execute(
                                sa.select(
                                    User.id,
                                    User.access_token_digest,
                                    User.created_at,
                                    User.updated_at,
                                ).where(User.id == user_id)
                            )
                        ).one()
                    )
                    conversation_rows = (
                        await verification_session.execute(
                            sa.select(
                                Conversation.id,
                                Conversation.owner_id,
                                Conversation.title,
                                Conversation.next_message_sequence,
                                Conversation.created_at,
                                Conversation.updated_at,
                            )
                            .where(
                                Conversation.id.in_(deletion_snapshot_ids)
                            )
                            .order_by(Conversation.id)
                        )
                    ).all()
                    message_rows = (
                        await verification_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(
                                Message.conversation_id.in_(
                                    deletion_snapshot_ids
                                )
                            )
                            .order_by(
                                Message.conversation_id,
                                Message.sequence_number,
                            )
                        )
                    ).all()
                return (
                    owner_user,
                    {
                        row.id: tuple(row)
                        for row in conversation_rows
                    },
                    [tuple(row) for row in message_rows],
                )

            state_before_delete = await deletion_state()
            target_before_delete = state_before_delete[1][deletion_target_id]
            target_messages_before_delete = [
                row
                for row in state_before_delete[2]
                if row[1] == deletion_target_id
            ]
            assert target_before_delete[1] == user_id
            assert target_before_delete[3] == 3
            assert [
                row[4] for row in target_messages_before_delete
            ] == [1, 2]
            assert {
                row[0] for row in target_messages_before_delete
            } == {
                UUID(deletion_target_payload["initial_message"]["id"]),
                UUID(deletion_target_append_payload["id"]),
            }

            foreign_delete = await client.delete(
                f"/api/v1/conversations/{deletion_target_id}",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
            )
            missing_delete = await client.delete(
                f"/api/v1/conversations/{uuid4()}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            expected_delete_not_found = {
                "code": "HTTP_ERROR",
                "message": "Conversation not found",
            }
            assert foreign_delete.status_code == 404
            assert missing_delete.status_code == 404
            assert foreign_delete.json()["error"] == expected_delete_not_found
            assert missing_delete.json()["error"] == expected_delete_not_found
            assert await deletion_state() == state_before_delete

            owner_delete = await client.delete(
                f"/api/v1/conversations/{deletion_target_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert owner_delete.status_code == 204
            assert owner_delete.content == b""

            deleted_get = await client.get(
                f"/api/v1/conversations/{deletion_target_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert deleted_get.status_code == 404
            assert deleted_get.json()["error"] == expected_delete_not_found

            deleted_message_history = await client.get(
                f"/api/v1/conversations/{deletion_target_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert deleted_message_history.status_code == 200
            assert deleted_message_history.json() == {
                "items": [],
                "next_cursor": None,
            }

            listing_after_delete = await client.get(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert listing_after_delete.status_code == 200
            assert deletion_target_id not in {
                UUID(item["id"])
                for item in listing_after_delete.json()["items"]
            }

            existing_get_after_delete = await client.get(
                f"/api/v1/conversations/{conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert existing_get_after_delete.status_code == 200
            assert existing_get_after_delete.json()["title"] == rename_title

            existing_message_history_after_delete = await client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert existing_message_history_after_delete.status_code == 200
            assert [
                item["sequence_number"]
                for item in existing_message_history_after_delete.json()["items"]
            ] == [1, 2]

            state_after_delete = await deletion_state()
            assert state_after_delete[0] == state_before_delete[0]
            assert deletion_target_id not in state_after_delete[1]
            assert {
                conversation_id: row
                for conversation_id, row in state_after_delete[1].items()
            } == {
                conversation_id: row
                for conversation_id, row in state_before_delete[1].items()
                if conversation_id != deletion_target_id
            }
            assert state_after_delete[2] == [
                row
                for row in state_before_delete[2]
                if row[1] != deletion_target_id
            ]
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory

    conversation_id = UUID(created_payload["id"])
    initial_message_payload = created_payload["initial_message"]
    assert set(created_payload) == {
        "id",
        "title",
        "is_pinned",
        "is_archived",
        "created_at",
        "updated_at",
        "initial_message",
    }
    assert created_payload["title"] == "  API integration  "
    assert set(initial_message_payload) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "sequence_number",
        "created_at",
        "updated_at",
        "attachments",
    }
    assert initial_message_payload["conversation_id"] == str(conversation_id)
    assert initial_message_payload["role"] == "user"
    assert initial_message_payload["content"] == "  Exact API content  "
    assert initial_message_payload["sequence_number"] == 1
    assert initial_message_payload["attachments"] == []

    assert set(appended_payload) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "sequence_number",
        "created_at",
        "updated_at",
        "attachments",
    }
    assert appended_payload["conversation_id"] == str(conversation_id)
    assert appended_payload["role"] == "user"
    assert appended_payload["content"] == "  Exact API follow-up  "
    assert appended_payload["sequence_number"] == 2
    assert appended_payload["attachments"] == []

    assert [
        item["sequence_number"] for item in first_message_page_payload["items"]
    ] == [1]
    assert first_message_page_payload["next_cursor"] == 1
    assert [
        item["sequence_number"] for item in second_message_page_payload["items"]
    ] == [2]
    assert second_message_page_payload["next_cursor"] is None
    uniform_empty_page = {"items": [], "next_cursor": None}
    assert empty_message_page_payload == uniform_empty_page
    assert missing_message_page_payload == uniform_empty_page
    assert foreign_message_page_payload == uniform_empty_page

    first_conversation_items = first_conversation_page_payload["items"]
    second_conversation_items = second_conversation_page_payload["items"]
    listed_conversation_items = first_conversation_items + second_conversation_items
    listed_conversation_ids = [
        UUID(item["id"]) for item in listed_conversation_items
    ]
    expected_conversation_ids = sorted(
        newer_ids,
        key=lambda value: value.int,
        reverse=True,
    ) + sorted(
        older_ids,
        key=lambda value: value.int,
        reverse=True,
    )
    assert listed_conversation_ids == expected_conversation_ids
    assert len(set(listed_conversation_ids)) == len(listed_conversation_ids)
    listed_ordering_keys = [
        (
            datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
            UUID(item["id"]).int,
        )
        for item in listed_conversation_items
    ]
    assert listed_ordering_keys == sorted(listed_ordering_keys, reverse=True)
    assert first_conversation_page_payload["next_cursor"] == {
        "updated_at": first_conversation_items[-1]["updated_at"],
        "id": first_conversation_items[-1]["id"],
    }
    assert second_conversation_page_payload["next_cursor"] is None
    assert [
        UUID(item["id"]) for item in foreign_conversation_page_payload["items"]
    ] == [UUID(foreign_conversation_payload["id"])]
    assert foreign_conversation_page_payload["next_cursor"] is None
    assert empty_conversation_page_payload == uniform_empty_page
    for item in listed_conversation_items:
        assert set(item) == {
            "id",
            "title",
            "is_pinned",
            "is_archived",
            "created_at",
            "updated_at",
        }
    renamed_list_item = next(
        item
        for item in listed_conversation_items
        if UUID(item["id"]) == conversation_id
    )
    assert renamed_list_item["title"] == rename_title

    async with AsyncSession(
        test_database_engine,
        expire_on_commit=False,
    ) as verification_session:
        stored_user = await verification_session.get(User, user_id)
        stored_conversation = await verification_session.get(
            Conversation,
            conversation_id,
        )
        stored_initial_message = await verification_session.get(
            Message,
            UUID(initial_message_payload["id"]),
        )
        stored_appended_message = await verification_session.get(
            Message,
            UUID(appended_payload["id"]),
        )
        stored_empty_conversation = await verification_session.get(
            Conversation,
            empty_conversation.id,
        )
        after_listing_rows = (
            await verification_session.execute(
                sa.select(
                    Conversation.id,
                    Conversation.owner_id,
                    Conversation.updated_at,
                    Conversation.next_message_sequence,
                ).where(Conversation.id.in_(owned_conversation_ids))
            )
        ).all()
        after_listing = {
            row.id: (
                row.owner_id,
                row.updated_at,
                row.next_message_sequence,
            )
            for row in after_listing_rows
        }

        assert stored_user is not None
        assert stored_user.access_token_digest is None
        stored_session = await verification_session.scalar(
            sa.select(UserSession).where(UserSession.user_id == user_id)
        )
        assert stored_session is not None
        assert stored_session.access_token_digest == digest_access_token(access_token)
        assert stored_session.access_token_digest != access_token
        assert stored_conversation is not None
        assert stored_conversation.owner_id == user_id
        assert stored_conversation.title == rename_title
        assert stored_conversation.next_message_sequence == 3
        assert stored_empty_conversation is not None
        assert stored_empty_conversation.owner_id == user_id
        assert stored_empty_conversation.next_message_sequence == 1
        assert after_listing == before_listing
        assert stored_initial_message is not None
        assert stored_initial_message.conversation_id == stored_conversation.id
        assert stored_initial_message.role is MessageRole.USER
        assert stored_initial_message.content == "  Exact API content  "
        assert stored_initial_message.sequence_number == 1

        assert stored_appended_message is not None
        assert stored_appended_message.conversation_id == stored_conversation.id
        assert stored_appended_message.role is MessageRole.USER
        assert stored_appended_message.content == "  Exact API follow-up  "
        assert stored_appended_message.sequence_number == 2

        first_page = await MessageService(verification_session).list_for_owner(
            stored_conversation.owner_id,
            stored_conversation.id,
            MessagePagination(limit=1),
        )
        assert [message.sequence_number for message in first_page.items] == [1]
        assert first_page.next_cursor is not None
        second_page = await MessageService(verification_session).list_for_owner(
            stored_conversation.owner_id,
            stored_conversation.id,
            MessagePagination(limit=1, cursor=first_page.next_cursor),
        )
        assert [message.sequence_number for message in second_page.items] == [2]
        assert second_page.next_cursor is None

        second_owner_conversations = await ConversationService(
            verification_session
        ).list_for_owner(UUID(second_user_payload["id"]))
        assert [
            conversation.id for conversation in second_owner_conversations.items
        ] == [UUID(foreign_conversation_payload["id"])]


@pytest.mark.asyncio
async def test_authenticated_local_model_listing_is_database_read_only(
    test_database_engine: AsyncEngine,
):
    class FakeLocalRuntime:
        runtime_id = "integration-local"

        def __init__(self) -> None:
            self.discovery_calls = 0

        async def discover_models(self) -> tuple[RuntimeModel, ...]:
            self.discovery_calls += 1
            return (
                RuntimeModel(
                    reference="/private/runtime/model:32b",
                    display_name="Integration 32B",
                    family="IntegrationFamily",
                    parameter_class="32B",
                    capabilities=("chat", "text-generation"),
                ),
            )

    def normalized_schema(value):
        if isinstance(value, dict):
            return tuple(
                sorted(
                    (str(key), normalized_schema(item))
                    for key, item in value.items()
                )
            )
        if isinstance(value, (list, tuple, set)):
            return tuple(sorted(repr(normalized_schema(item)) for item in value))
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    async def database_state() -> tuple:
        async with AsyncSession(test_database_engine) as session:
            users = (
                await session.execute(
                    sa.select(
                        User.id,
                        User.access_token_digest,
                        User.created_at,
                        User.updated_at,
                    ).order_by(User.id)
                )
            ).all()
            conversations = (
                await session.execute(
                    sa.select(
                        Conversation.id,
                        Conversation.owner_id,
                        Conversation.title,
                        Conversation.next_message_sequence,
                        Conversation.created_at,
                        Conversation.updated_at,
                    ).order_by(Conversation.id)
                )
            ).all()
            messages = (
                await session.execute(
                    sa.select(
                        Message.id,
                        Message.conversation_id,
                        Message.role,
                        Message.content,
                        Message.sequence_number,
                        Message.created_at,
                        Message.updated_at,
                    ).order_by(Message.id)
                )
            ).all()
        return (
            tuple(tuple(row) for row in users),
            tuple(tuple(row) for row in conversations),
            tuple(tuple(row) for row in messages),
        )

    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    runtime = FakeLocalRuntime()
    catalog = ModelCatalog((runtime,))
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    previous_catalog = getattr(app.state, "model_catalog", missing)
    app.state.db_session_factory = session_factory
    app.state.model_catalog = catalog

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            provisioned = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            assert provisioned.status_code == 201
            access_token = provisioned.json()["access_token"]

            schema_before = normalized_schema(
                await _schema_snapshot(test_database_engine)
            )
            state_before = await database_state()

            unauthenticated = await client.get("/api/v1/ai/models")
            assert unauthenticated.status_code == 401
            assert runtime.discovery_calls == 0

            authenticated = await client.get(
                "/api/v1/ai/models",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert authenticated.status_code == 200
            payload = authenticated.json()
            assert len(payload["items"]) == 1
            model = payload["items"][0]
            assert model == {
                "model_id": model["model_id"],
                "display_name": "Integration 32B",
                "runtime_id": "integration-local",
                "modality": "text",
                "family": "IntegrationFamily",
                "parameter_class": "32B",
                "capabilities": ["chat", "text_generation"],
                "context_window": None,
                "quantization": None,
                    "estimated_vram_bytes": None,
                    "availability": "available",
                    "scale_class": None,
                    "required_vram_bytes": None,
                    "required_ram_bytes": None,
                    "installed": True,
                    "runnable_now": True,
                    "future_capable": False,
                    "hardware_class": None,
                    "fallback_model_id": None,
                }
            assert model["model_id"].startswith("integration-local:")
            assert "/private/runtime/model:32b" not in authenticated.text
            assert runtime.discovery_calls == 1

            assert await database_state() == state_before
            assert normalized_schema(
                await _schema_snapshot(test_database_engine)
            ) == schema_before
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory
        if previous_catalog is missing:
            delattr(app.state, "model_catalog")
        else:
            app.state.model_catalog = previous_catalog


@pytest.mark.asyncio
async def test_authenticated_conversation_generation_is_owner_scoped_and_stale_safe(
    test_database_engine: AsyncEngine,
    monkeypatch,
):
    class FakeLocalTextRuntime:
        runtime_id = "integration-local"

        def __init__(self) -> None:
            self.mode = "success"
            self.discovery_calls = 0
            self.generation_calls: list[tuple] = []
            self.stale_owner_id: UUID | None = None
            self.stale_conversation_id: UUID | None = None

        async def discover_models(self) -> tuple[RuntimeModel, ...]:
            self.discovery_calls += 1
            return (
                RuntimeModel(
                    reference="/private/runtime/model:70b",
                    display_name="Integration 70B",
                    parameter_class="70B+",
                    capabilities=(
                        ()
                        if self.mode == "descriptor-unsupported"
                        else ("chat", "text-generation")
                    ),
                    availability=(
                        ModelAvailability.UNAVAILABLE
                        if self.mode == "descriptor-unavailable"
                        else ModelAvailability.AVAILABLE
                    ),
                ),
            )

        async def generate_text(
            self,
            runtime_reference,
            messages,
            *,
            max_output_tokens,
            temperature=None,
            seed=None,
            top_p=None,
            top_k=None,
            min_p=None,
            repeat_penalty=None,
            repeat_last_n=None,
            typical_p=None,
            presence_penalty=None,
            frequency_penalty=None,
            stop_sequences=None,
        ) -> TextGenerationResult:
            self.generation_calls.append(
                (
                    runtime_reference,
                    messages,
                    max_output_tokens,
                    temperature,
                    seed,
                    top_p,
                    top_k,
                    min_p,
                    repeat_penalty,
                    repeat_last_n,
                    typical_p,
                    presence_penalty,
                    frequency_penalty,
                    stop_sequences,
                )
            )
            if self.mode == "unavailable":
                raise TextGenerationRuntimeUnavailableError(
                    "secret local runtime detail"
                )
            if self.mode == "stale":
                assert self.stale_owner_id is not None
                assert self.stale_conversation_id is not None
                async with AsyncSession(
                    test_database_engine,
                    expire_on_commit=False,
                ) as concurrent_session:
                    intervening = await MessageService(
                        concurrent_session
                    ).append_for_owner(
                        self.stale_owner_id,
                        self.stale_conversation_id,
                        MessageRole.USER,
                        "intervening user message",
                    )
                    assert intervening is not None
            if self.mode == "boundary":
                return TextGenerationResult(
                    content="é" * MAX_MESSAGE_CONTENT_CHARACTERS
                )
            if self.mode == "oversized":
                return TextGenerationResult(
                    content="x" * (MAX_MESSAGE_CONTENT_CHARACTERS + 1)
                )
            return TextGenerationResult(content="  exact local answer  ")

    def normalized_schema(value):
        if isinstance(value, dict):
            return tuple(
                sorted(
                    (str(key), normalized_schema(item))
                    for key, item in value.items()
                )
            )
        if isinstance(value, (list, tuple, set)):
            return tuple(sorted(repr(normalized_schema(item)) for item in value))
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    async def persisted_conversation(conversation_id: UUID):
        async with AsyncSession(
            test_database_engine,
            expire_on_commit=False,
        ) as session:
            conversation = await session.get(Conversation, conversation_id)
            messages = (
                await session.execute(
                    sa.select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.sequence_number)
                )
            ).scalars().all()
            return conversation, tuple(messages)

    runtime = FakeLocalTextRuntime()

    class PreContextRaceMessageService(MessageService):
        async def list_generation_context_for_owner(
            self,
            owner_id,
            conversation_id,
            *,
            max_messages,
            max_context_characters,
        ):
            if runtime.mode == "pre-context-stale":
                runtime.mode = "success"
                async with AsyncSession(
                    test_database_engine,
                    expire_on_commit=False,
                ) as concurrent_session:
                    intervening = await MessageService(
                        concurrent_session
                    ).append_for_owner(
                        owner_id,
                        conversation_id,
                        MessageRole.USER,
                        "pre-context intervening user message",
                    )
                    assert intervening is not None
            return await super().list_generation_context_for_owner(
                owner_id,
                conversation_id,
                max_messages=max_messages,
                max_context_characters=max_context_characters,
            )

    monkeypatch.setattr(
        generation_module,
        "MessageService",
        PreContextRaceMessageService,
    )
    catalog = ModelCatalog((runtime,))
    generation_router = TextGenerationRouter((runtime,))
    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    previous_catalog = getattr(app.state, "model_catalog", missing)
    previous_router = getattr(app.state, "text_generation_router", missing)
    previous_admission = getattr(
        app.state, "generation_admission_controller", missing
    )
    previous_duration = getattr(
        app.state, "generation_max_duration_seconds", missing
    )
    app.state.db_session_factory = session_factory
    app.state.model_catalog = catalog
    app.state.text_generation_router = generation_router
    app.state.generation_admission_controller = (
        GenerationAdmissionController(1)
    )
    app.state.generation_max_duration_seconds = 180.0

    try:
        schema_before = normalized_schema(
            await _schema_snapshot(test_database_engine)
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            owner_response = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            foreign_response = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            assert owner_response.status_code == 201
            assert foreign_response.status_code == 201
            owner = owner_response.json()
            foreign = foreign_response.json()
            owner_headers = {
                "Authorization": f"Bearer {owner['access_token']}"
            }
            foreign_headers = {
                "Authorization": f"Bearer {foreign['access_token']}"
            }

            oversized_client_message = "private-oversized-fragment" + "x" * (
                MAX_MESSAGE_CONTENT_CHARACTERS
                + 1
                - len("private-oversized-fragment")
            )
            for field in ("system_prompt", "initial_message"):
                rejected_bootstrap = await client.post(
                    "/api/v1/conversations",
                    headers=owner_headers,
                    json={
                        "initial_message": "initial",
                        field: oversized_client_message,
                    },
                )
                assert rejected_bootstrap.status_code == 413
                assert rejected_bootstrap.json()["error"] == {
                    "code": "HTTP_ERROR",
                    "message": "Message content is too large",
                }
                assert "private-oversized-fragment" not in (
                    rejected_bootstrap.text
                )
            before_valid_bootstrap = await client.get(
                "/api/v1/conversations",
                headers=owner_headers,
            )
            assert before_valid_bootstrap.status_code == 200
            assert before_valid_bootstrap.json()["items"] == []

            owner_created = await client.post(
                "/api/v1/conversations",
                headers=owner_headers,
                json={
                    "title": "Generation target",
                    "system_prompt": "  exact system prompt  ",
                    "initial_message": "first user prompt",
                },
            )
            foreign_created = await client.post(
                "/api/v1/conversations",
                headers=foreign_headers,
                json={
                    "title": "Foreign generation target",
                    "initial_message": "foreign prompt",
                },
            )
            assert owner_created.status_code == 201
            assert foreign_created.status_code == 201
            conversation_id = UUID(owner_created.json()["id"])
            foreign_conversation_id = UUID(foreign_created.json()["id"])

            before_oversized_client, messages_before_oversized_client = (
                await persisted_conversation(conversation_id)
            )
            assert before_oversized_client is not None
            rejected_append = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers=owner_headers,
                json={"content": oversized_client_message},
            )
            rejected_generation_user = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": f"integration-local:{'a' * 24}",
                    "user_message": oversized_client_message,
                },
            )
            for rejected in (rejected_append, rejected_generation_user):
                assert rejected.status_code == 413
                assert rejected.json()["error"] == {
                    "code": "HTTP_ERROR",
                    "message": "Message content is too large",
                }
                assert "private-oversized-fragment" not in rejected.text
            after_oversized_client, messages_after_oversized_client = (
                await persisted_conversation(conversation_id)
            )
            assert after_oversized_client is not None
            assert after_oversized_client.next_message_sequence == (
                before_oversized_client.next_message_sequence
            )
            assert [message.id for message in messages_after_oversized_client] == [
                message.id for message in messages_before_oversized_client
            ]
            assert runtime.discovery_calls == 0
            assert runtime.generation_calls == []

            unauthenticated = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                json={
                    "model_id": f"integration-local:{'a' * 24}",
                    "user_message": "must not persist",
                },
            )
            assert unauthenticated.status_code == 401
            assert runtime.discovery_calls == 0
            assert runtime.generation_calls == []

            models = await client.get(
                "/api/v1/ai/models",
                headers=owner_headers,
            )
            assert models.status_code == 200
            model_id = models.json()["items"][0]["model_id"]

            generated = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "  second user prompt  ",
                    "max_output_tokens": 128,
                    "temperature": 0.25,
                    "seed": 42,
                    "top_p": 0.9,
                    "top_k": 40,
                    "min_p": 0.05,
                    "repeat_penalty": 1.1,
                    "repeat_last_n": 64,
                    "typical_p": 0.7,
                    "presence_penalty": 1.5,
                    "frequency_penalty": 0.75,
                    "stop_sequences": ["\n", "END", "\n"],
                },
            )
            assert generated.status_code == 201
            assert generated.json() == {
                "model_id": model_id,
                "message": {
                    "id": generated.json()["message"]["id"],
                    "conversation_id": str(conversation_id),
                    "role": "assistant",
                    "content": "  exact local answer  ",
                    "sequence_number": 4,
                    "created_at": generated.json()["message"]["created_at"],
                    "updated_at": generated.json()["message"]["updated_at"],
                    "attachments": [],
                },
            }
            assert "/private/runtime/model:70b" not in generated.text
            (
                runtime_reference,
                context,
                output_bound,
                temperature,
                seed,
                top_p,
                top_k,
                min_p,
                repeat_penalty,
                repeat_last_n,
                typical_p,
                presence_penalty,
                frequency_penalty,
                stop_sequences,
            ) = runtime.generation_calls[0]
            assert runtime_reference == "/private/runtime/model:70b"
            assert [(message.role.value, message.content) for message in context] == [
                ("system", "  exact system prompt  "),
                ("user", "first user prompt"),
                ("user", "  second user prompt  "),
            ]
            assert output_bound == 128
            assert temperature == 0.25
            assert seed == 42
            assert top_p == 0.9
            assert top_k == 40
            assert min_p == 0.05
            assert repeat_penalty == 1.1
            assert repeat_last_n == 64
            assert typical_p == 0.7
            assert presence_penalty == 1.5
            assert frequency_penalty == 0.75
            assert stop_sequences == ["\n", "END", "\n"]

            foreign_attempt = await client.post(
                f"/api/v1/conversations/{foreign_conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "must not persist for foreign owner",
                },
            )
            missing_attempt = await client.post(
                f"/api/v1/conversations/{uuid4()}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "must not persist for missing conversation",
                },
            )
            assert foreign_attempt.status_code == 404
            assert missing_attempt.status_code == 404
            assert foreign_attempt.json()["error"] == missing_attempt.json()["error"]
            assert len(runtime.generation_calls) == 1

            unknown_model = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": f"integration-local:{'f' * 24}",
                    "user_message": "unknown-model user prompt",
                },
            )
            assert unknown_model.status_code == 404
            assert unknown_model.json()["error"]["message"] == "Model not found"
            assert len(runtime.generation_calls) == 1

            runtime.mode = "descriptor-unavailable"
            unavailable_descriptor = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert unavailable_descriptor.status_code == 503
            assert unavailable_descriptor.json()["error"]["message"] == (
                "Local model runtime unavailable"
            )
            assert len(runtime.generation_calls) == 1

            before_unavailable, messages_before_unavailable = (
                await persisted_conversation(conversation_id)
            )
            assert before_unavailable is not None
            runtime.mode = "unavailable"
            unavailable = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "runtime-failure user prompt",
                },
            )
            assert unavailable.status_code == 503
            assert unavailable.json()["error"]["message"] == (
                "Local model runtime unavailable"
            )
            after_unavailable, messages_after_unavailable = (
                await persisted_conversation(conversation_id)
            )
            assert after_unavailable is not None
            assert after_unavailable.next_message_sequence == (
                before_unavailable.next_message_sequence + 1
            )
            assert [
                (message.role, message.content, message.sequence_number)
                for message in messages_after_unavailable
            ] == [
                (message.role, message.content, message.sequence_number)
                for message in messages_before_unavailable
            ] + [
                (
                    MessageRole.USER,
                    "runtime-failure user prompt",
                    before_unavailable.next_message_sequence,
                )
            ]
            assert len(runtime.generation_calls) == 2

            runtime.mode = "success"
            retry = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert retry.status_code == 201
            assert retry.json()["message"]["role"] == "assistant"
            assert retry.json()["message"]["sequence_number"] == 7
            assert len(runtime.generation_calls) == 3
            assert runtime.generation_calls[2][2] == 1024
            assert runtime.generation_calls[2][3] is None
            assert runtime.generation_calls[2][4] is None
            assert runtime.generation_calls[2][5] is None
            assert runtime.generation_calls[2][6] is None
            assert runtime.generation_calls[2][7] is None
            assert runtime.generation_calls[2][8] is None
            assert runtime.generation_calls[2][9] is None
            assert runtime.generation_calls[2][10] is None
            assert runtime.generation_calls[2][11] is None
            assert runtime.generation_calls[2][12] is None
            assert runtime.generation_calls[2][13] is None

            runtime.mode = "pre-context-stale"
            pre_context_stale = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "pre-context request user message",
                },
            )
            assert pre_context_stale.status_code == 409
            assert pre_context_stale.json()["error"]["message"] == (
                "Conversation changed during generation"
            )
            assert len(runtime.generation_calls) == 3

            runtime.mode = "stale"
            runtime.stale_owner_id = UUID(owner["id"])
            runtime.stale_conversation_id = conversation_id
            stale = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "during-inference request user message",
                },
            )
            assert stale.status_code == 409
            assert stale.json()["error"]["message"] == (
                "Conversation changed during generation"
            )
            assert len(runtime.generation_calls) == 4

            unsupported_created = await client.post(
                "/api/v1/conversations",
                headers=owner_headers,
                json={
                    "title": "Capability retry target",
                    "initial_message": "initial capability prompt",
                },
            )
            assert unsupported_created.status_code == 201
            unsupported_conversation_id = UUID(
                unsupported_created.json()["id"]
            )
            runtime.mode = "descriptor-unsupported"
            unsupported = await client.post(
                "/api/v1/conversations/"
                f"{unsupported_conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "committed capability retry prompt",
                },
            )
            assert unsupported.status_code == 409
            assert unsupported.json()["error"]["message"] == (
                "Model does not support text generation"
            )
            assert len(runtime.generation_calls) == 4
            unsupported_state, unsupported_messages = (
                await persisted_conversation(unsupported_conversation_id)
            )
            assert unsupported_state is not None
            assert unsupported_state.next_message_sequence == 3
            assert [
                (message.role, message.content, message.sequence_number)
                for message in unsupported_messages
            ] == [
                (MessageRole.USER, "initial capability prompt", 1),
                (
                    MessageRole.USER,
                    "committed capability retry prompt",
                    2,
                ),
            ]

            runtime.mode = "success"
            capability_retry = await client.post(
                "/api/v1/conversations/"
                f"{unsupported_conversation_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert capability_retry.status_code == 201
            assert capability_retry.json()["message"]["sequence_number"] == 3
            assert len(runtime.generation_calls) == 5

            boundary_created = await client.post(
                "/api/v1/conversations",
                headers=owner_headers,
                json={"initial_message": "boundary assistant target"},
            )
            assert boundary_created.status_code == 201
            boundary_conversation_id = UUID(boundary_created.json()["id"])
            runtime.mode = "boundary"
            boundary_assistant = await client.post(
                "/api/v1/conversations/"
                f"{boundary_conversation_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert boundary_assistant.status_code == 201
            assert len(boundary_assistant.json()["message"]["content"]) == (
                MAX_MESSAGE_CONTENT_CHARACTERS
            )
            boundary_state, boundary_messages = await persisted_conversation(
                boundary_conversation_id
            )
            assert boundary_state is not None
            assert boundary_state.next_message_sequence == 3
            assert [message.role for message in boundary_messages] == [
                MessageRole.USER,
                MessageRole.ASSISTANT,
            ]
            assert len(boundary_messages[1].content) == (
                MAX_MESSAGE_CONTENT_CHARACTERS
            )

            oversized_created = await client.post(
                "/api/v1/conversations",
                headers=owner_headers,
                json={"initial_message": "oversized assistant target"},
            )
            assert oversized_created.status_code == 201
            oversized_conversation_id = UUID(oversized_created.json()["id"])
            runtime.mode = "oversized"
            oversized_assistant = await client.post(
                "/api/v1/conversations/"
                f"{oversized_conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "committed oversized-assistant user",
                },
            )
            assert oversized_assistant.status_code == 503
            assert oversized_assistant.json()["error"] == {
                "code": "HTTP_ERROR",
                "message": "Local model runtime unavailable",
            }
            oversized_state, oversized_messages = await persisted_conversation(
                oversized_conversation_id
            )
            assert oversized_state is not None
            assert oversized_state.next_message_sequence == 3
            assert [
                (message.role, message.content, message.sequence_number)
                for message in oversized_messages
            ] == [
                (MessageRole.USER, "oversized assistant target", 1),
                (
                    MessageRole.USER,
                    "committed oversized-assistant user",
                    2,
                ),
            ]

            runtime.mode = "success"
            oversized_retry = await client.post(
                "/api/v1/conversations/"
                f"{oversized_conversation_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert oversized_retry.status_code == 201
            assert oversized_retry.json()["message"]["sequence_number"] == 3

            history_items = []
            cursor = None
            while True:
                parameters = {"limit": 4}
                if cursor is not None:
                    parameters["cursor"] = cursor
                history = await client.get(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    headers=owner_headers,
                    params=parameters,
                )
                assert history.status_code == 200
                history_page = history.json()
                history_items.extend(history_page["items"])
                cursor = history_page["next_cursor"]
                if cursor is None:
                    break

            assert [
                (item["role"], item["content"], item["sequence_number"])
                for item in history_items
            ] == [
                ("system", "  exact system prompt  ", 1),
                ("user", "first user prompt", 2),
                ("user", "  second user prompt  ", 3),
                ("assistant", "  exact local answer  ", 4),
                ("user", "unknown-model user prompt", 5),
                ("user", "runtime-failure user prompt", 6),
                ("assistant", "  exact local answer  ", 7),
                ("user", "pre-context request user message", 8),
                ("user", "pre-context intervening user message", 9),
                ("user", "during-inference request user message", 10),
                ("user", "intervening user message", 11),
            ]
            stored_owner, stored_messages = await persisted_conversation(
                conversation_id
            )
            stored_foreign, foreign_messages = await persisted_conversation(
                foreign_conversation_id
            )
            assert stored_owner is not None
            assert stored_owner.owner_id == UUID(owner["id"])
            assert stored_owner.next_message_sequence == 12
            assert len(stored_messages) == 11
            assert stored_foreign is not None
            assert stored_foreign.owner_id == UUID(foreign["id"])
            assert stored_foreign.next_message_sequence == 2
            assert [
                (message.role, message.content, message.sequence_number)
                for message in foreign_messages
            ] == [(MessageRole.USER, "foreign prompt", 1)]

            async def seed_api_context(contents: tuple[str, ...]) -> UUID:
                async with AsyncSession(
                    test_database_engine,
                    expire_on_commit=False,
                ) as seed_session:
                    seeded = Conversation(
                        owner_id=UUID(owner["id"]),
                        title="API generation context boundary",
                        next_message_sequence=len(contents) + 1,
                    )
                    seed_session.add(seeded)
                    await seed_session.flush()
                    seed_session.add_all(
                        Message(
                            conversation_id=seeded.id,
                            role=MessageRole.USER,
                            content=content,
                            sequence_number=sequence_number,
                        )
                        for sequence_number, content in enumerate(
                            contents,
                            start=1,
                        )
                    )
                    await seed_session.commit()
                    return seeded.id

            runtime.mode = "success"
            valid_count_id = await seed_api_context(("x",) * 100)
            calls_before_context_boundaries = len(runtime.generation_calls)
            valid_count_generation = await client.post(
                "/api/v1/conversations/"
                f"{valid_count_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert valid_count_generation.status_code == 201
            assert len(runtime.generation_calls) == (
                calls_before_context_boundaries + 1
            )
            assert len(runtime.generation_calls[-1][1]) == 100

            count_overflow_id = await seed_api_context(("x",) * 101)
            calls_before_count_overflow = len(runtime.generation_calls)
            count_overflow = await client.post(
                "/api/v1/conversations/"
                f"{count_overflow_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert count_overflow.status_code == 413
            assert count_overflow.json()["error"] == {
                "code": "HTTP_ERROR",
                "message": "Conversation context is too large",
            }
            assert len(runtime.generation_calls) == calls_before_count_overflow

            exact_context = "a" * 99_997 + "é界🧠"
            exact_characters_id = await seed_api_context((exact_context,))
            exact_characters = await client.post(
                "/api/v1/conversations/"
                f"{exact_characters_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert exact_characters.status_code == 201
            assert runtime.generation_calls[-1][1][0].content == exact_context

            for oversized_contents in (
                (exact_context, "x"),
                ("a" * 60_000, "界" * 60_000),
            ):
                oversized_context_id = await seed_api_context(
                    oversized_contents
                )
                calls_before_character_overflow = len(
                    runtime.generation_calls
                )
                character_overflow = await client.post(
                    "/api/v1/conversations/"
                    f"{oversized_context_id}/messages/generate",
                    headers=owner_headers,
                    json={"model_id": model_id},
                )
                assert character_overflow.status_code == 413
                assert character_overflow.json()["error"] == {
                    "code": "HTTP_ERROR",
                    "message": "Conversation context is too large",
                }
                assert len(runtime.generation_calls) == (
                    calls_before_character_overflow
                )
                lowered_error = character_overflow.text.lower()
                for internal_detail in (
                    "candidate",
                    "char_length",
                    "budget",
                    "sql",
                ):
                    assert internal_detail not in lowered_error

            listed = await client.get(
                "/api/v1/conversations",
                headers=owner_headers,
            )
            assert listed.status_code == 200
            assert conversation_id in {
                UUID(item["id"]) for item in listed.json()["items"]
            }

        assert normalized_schema(
            await _schema_snapshot(test_database_engine)
        ) == schema_before
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory
        if previous_catalog is missing:
            delattr(app.state, "model_catalog")
        else:
            app.state.model_catalog = previous_catalog
        if previous_router is missing:
            delattr(app.state, "text_generation_router")
        else:
            app.state.text_generation_router = previous_router
        if previous_admission is missing:
            delattr(app.state, "generation_admission_controller")
        else:
            app.state.generation_admission_controller = (
                previous_admission
            )
        if previous_duration is missing:
            delattr(app.state, "generation_max_duration_seconds")
        else:
            app.state.generation_max_duration_seconds = previous_duration



@pytest.mark.asyncio
async def test_generation_admission_is_user_scoped_globally_bounded_and_rotation_safe(
    test_database_engine: AsyncEngine,
):
    class BlockingLocalTextRuntime:
        runtime_id = "admission-local"

        def __init__(self) -> None:
            self.active_calls = 0
            self.maximum_active_calls = 0
            self.stage_calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.expected_active = 1

        def prepare(self, expected_active: int) -> None:
            assert self.active_calls == 0
            self.maximum_active_calls = 0
            self.stage_calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.expected_active = expected_active

        async def discover_models(self) -> tuple[RuntimeModel, ...]:
            return (
                RuntimeModel(
                    reference="/private/runtime/admission:latest",
                    display_name="Admission integration model",
                    capabilities=("chat", "text-generation"),
                ),
            )

        async def generate_text(
            self,
            _runtime_reference,
            _messages,
            *,
            max_output_tokens,
            temperature=None,
            seed=None,
            top_p=None,
            top_k=None,
            min_p=None,
            repeat_penalty=None,
            repeat_last_n=None,
            typical_p=None,
            presence_penalty=None,
            frequency_penalty=None,
            stop_sequences=None,
        ) -> TextGenerationResult:
            assert max_output_tokens == 1024
            self.stage_calls += 1
            self.active_calls += 1
            self.maximum_active_calls = max(
                self.maximum_active_calls,
                self.active_calls,
            )
            if self.active_calls == self.expected_active:
                self.entered.set()
            try:
                await self.release.wait()
                return TextGenerationResult(content="admitted answer")
            finally:
                self.active_calls -= 1

    runtime = BlockingLocalTextRuntime()
    catalog = ModelCatalog((runtime,))
    generation_router = TextGenerationRouter((runtime,))
    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    previous_catalog = getattr(app.state, "model_catalog", missing)
    previous_router = getattr(app.state, "text_generation_router", missing)
    previous_admission = getattr(
        app.state,
        "generation_admission_controller",
        missing,
    )
    previous_duration = getattr(
        app.state, "generation_max_duration_seconds", missing
    )
    app.state.db_session_factory = session_factory
    app.state.model_catalog = catalog
    app.state.text_generation_router = generation_router
    app.state.generation_admission_controller = GenerationAdmissionController(1)
    app.state.generation_max_duration_seconds = 180.0

    async def create_conversation(client, headers, initial_message):
        response = await client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"initial_message": initial_message},
        )
        assert response.status_code == 201
        return UUID(response.json()["id"])

    async def assert_busy(response):
        assert response.status_code == 429
        assert response.json()["error"] == {
            "code": "HTTP_ERROR",
            "message": "Generation capacity is busy",
        }
        assert "active" not in response.text.lower()
        assert "capacity configuration" not in response.text.lower()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            provisioned = []
            for _position in range(3):
                response = await client.post(
                    "/api/v1/users",
                    headers=_PROVISIONING_HEADERS,
                )
                assert response.status_code == 201
                provisioned.append(response.json())

            headers = [
                {"Authorization": f"Bearer {user['access_token']}"}
                for user in provisioned
            ]
            models = await client.get(
                "/api/v1/ai/models",
                headers=headers[0],
            )
            assert models.status_code == 200
            model_id = models.json()["items"][0]["model_id"]

            same_conversation = await create_conversation(
                client,
                headers[0],
                "same-conversation initial",
            )
            runtime.prepare(1)
            first = asyncio.create_task(
                client.post(
                    f"/api/v1/conversations/{same_conversation}/messages/generate",
                    headers=headers[0],
                    json={"model_id": model_id},
                )
            )
            await runtime.entered.wait()
            same_user_busy = await client.post(
                f"/api/v1/conversations/{same_conversation}/messages/generate",
                headers=headers[0],
                json={
                    "model_id": model_id,
                    "user_message": "must not persist same conversation",
                },
            )
            await assert_busy(same_user_busy)
            assert runtime.stage_calls == 1
            runtime.release.set()
            assert (await first).status_code == 201
            assert runtime.active_calls == 0

            same_history = await client.get(
                f"/api/v1/conversations/{same_conversation}/messages",
                headers=headers[0],
            )
            assert same_history.status_code == 200
            assert "must not persist same conversation" not in {
                item["content"] for item in same_history.json()["items"]
            }

            first_owned = await create_conversation(
                client,
                headers[0],
                "first owned initial",
            )
            second_owned = await create_conversation(
                client,
                headers[0],
                "second owned initial",
            )
            runtime.prepare(1)
            across_conversations = asyncio.create_task(
                client.post(
                    f"/api/v1/conversations/{first_owned}/messages/generate",
                    headers=headers[0],
                    json={"model_id": model_id},
                )
            )
            await runtime.entered.wait()
            second_conversation_busy = await client.post(
                f"/api/v1/conversations/{second_owned}/messages/generate",
                headers=headers[0],
                json={
                    "model_id": model_id,
                    "user_message": "must not persist other conversation",
                },
            )
            await assert_busy(second_conversation_busy)

            rotated = await client.post(
                "/api/v1/users/me/access-token/rotate",
                headers=headers[0],
            )
            assert rotated.status_code == 200
            headers[0] = {
                "Authorization": f"Bearer {rotated.json()['access_token']}"
            }
            rotated_token_busy = await client.post(
                f"/api/v1/conversations/{second_owned}/messages/generate",
                headers=headers[0],
                json={"model_id": model_id},
            )
            await assert_busy(rotated_token_busy)
            assert runtime.stage_calls == 1
            runtime.release.set()
            assert (await across_conversations).status_code == 201
            assert runtime.active_calls == 0

            second_history = await client.get(
                f"/api/v1/conversations/{second_owned}/messages",
                headers=headers[0],
            )
            assert second_history.status_code == 200
            assert [
                item["content"] for item in second_history.json()["items"]
            ] == ["second owned initial"]

            app.state.generation_admission_controller = (
                GenerationAdmissionController(2)
            )
            global_conversations = [
                await create_conversation(
                    client,
                    headers[position],
                    f"global initial {position}",
                )
                for position in range(3)
            ]
            runtime.prepare(2)
            first_global = asyncio.create_task(
                client.post(
                    "/api/v1/conversations/"
                    f"{global_conversations[0]}/messages/generate",
                    headers=headers[0],
                    json={"model_id": model_id},
                )
            )
            second_global = asyncio.create_task(
                client.post(
                    "/api/v1/conversations/"
                    f"{global_conversations[1]}/messages/generate",
                    headers=headers[1],
                    json={"model_id": model_id},
                )
            )
            await runtime.entered.wait()
            globally_busy = await client.post(
                "/api/v1/conversations/"
                f"{global_conversations[2]}/messages/generate",
                headers=headers[2],
                json={
                    "model_id": model_id,
                    "user_message": "must not persist global rejection",
                },
            )
            await assert_busy(globally_busy)
            assert runtime.stage_calls == 2
            assert runtime.maximum_active_calls == 2
            runtime.release.set()
            global_results = await asyncio.gather(first_global, second_global)
            assert [response.status_code for response in global_results] == [
                201,
                201,
            ]
            assert runtime.active_calls == 0
            assert (
                app.state.generation_admission_controller._active_users
                == set()
            )
            assert app.state.generation_admission_controller._active_count == 0

            rejected_history = await client.get(
                "/api/v1/conversations/"
                f"{global_conversations[2]}/messages",
                headers=headers[2],
            )
            assert rejected_history.status_code == 200
            assert [
                item["content"] for item in rejected_history.json()["items"]
            ] == ["global initial 2"]
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory
        if previous_catalog is missing:
            delattr(app.state, "model_catalog")
        else:
            app.state.model_catalog = previous_catalog
        if previous_router is missing:
            delattr(app.state, "text_generation_router")
        else:
            app.state.text_generation_router = previous_router
        if previous_admission is missing:
            delattr(app.state, "generation_admission_controller")
        else:
            app.state.generation_admission_controller = previous_admission
        if previous_duration is missing:
            delattr(app.state, "generation_max_duration_seconds")
        else:
            app.state.generation_max_duration_seconds = previous_duration


@pytest.mark.asyncio
async def test_generation_deadline_preserves_retry_and_releases_postgres_resources(
    test_database_engine: AsyncEngine,
    monkeypatch,
):
    class DeadlineLocalTextRuntime:
        runtime_id = "deadline-local"

        def __init__(self) -> None:
            self.block = True
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.active_calls = 0

        async def discover_models(self) -> tuple[RuntimeModel, ...]:
            return (
                RuntimeModel(
                    reference="/private/runtime/deadline-model",
                    display_name="Deadline model",
                    parameter_class="7B",
                    capabilities=("chat", "text-generation"),
                    availability=ModelAvailability.AVAILABLE,
                ),
            )

        async def generate_text(
            self,
            runtime_reference,
            messages,
            *,
            max_output_tokens,
            temperature=None,
            seed=None,
            top_p=None,
            top_k=None,
            min_p=None,
            repeat_penalty=None,
            repeat_last_n=None,
            typical_p=None,
            presence_penalty=None,
            frequency_penalty=None,
            stop_sequences=None,
        ) -> TextGenerationResult:
            self.active_calls += 1
            self.entered.set()
            try:
                if self.block:
                    await self.release.wait()
                return TextGenerationResult(content="deadline-safe answer")
            finally:
                self.active_calls -= 1

        def prepare_blocked_call(self) -> None:
            self.block = True
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

    runtime = DeadlineLocalTextRuntime()
    catalog = ModelCatalog((runtime,))
    generation_router = TextGenerationRouter((runtime,))
    admission = GenerationAdmissionController(1)
    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    previous_catalog = getattr(app.state, "model_catalog", missing)
    previous_router = getattr(app.state, "text_generation_router", missing)
    previous_admission = getattr(
        app.state,
        "generation_admission_controller",
        missing,
    )
    previous_duration = getattr(
        app.state,
        "generation_max_duration_seconds",
        missing,
    )
    app.state.db_session_factory = session_factory
    app.state.model_catalog = catalog
    app.state.text_generation_router = generation_router
    app.state.generation_admission_controller = admission
    app.state.generation_max_duration_seconds = 0.25
    disconnect_app = FastAPI()
    disconnect_app.include_router(
        conversations_module.router,
        prefix="/api/v1",
    )
    disconnect_app.state.db_session_factory = session_factory
    disconnect_app.state.model_catalog = catalog
    disconnect_app.state.text_generation_router = generation_router
    disconnect_app.state.generation_admission_controller = admission
    disconnect_app.state.generation_max_duration_seconds = 1.0

    async def create_conversation(client, headers, content):
        response = await client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"initial_message": content},
        )
        assert response.status_code == 201
        return UUID(response.json()["id"])

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            owner_response = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            other_response = await client.post(
                "/api/v1/users",
                headers=_PROVISIONING_HEADERS,
            )
            assert owner_response.status_code == 201
            assert other_response.status_code == 201
            owner_headers = {
                "Authorization": (
                    f"Bearer {owner_response.json()['access_token']}"
                )
            }
            other_headers = {
                "Authorization": (
                    f"Bearer {other_response.json()['access_token']}"
                )
            }
            owner_conversation = await create_conversation(
                client,
                owner_headers,
                "owner initial",
            )
            other_conversation = await create_conversation(
                client,
                other_headers,
                "other initial",
            )
            models = await client.get(
                "/api/v1/ai/models",
                headers=owner_headers,
            )
            assert models.status_code == 200
            model_id = models.json()["items"][0]["model_id"]

            timed_out_task = asyncio.create_task(
                client.post(
                    f"/api/v1/conversations/{owner_conversation}/messages/generate",
                    headers=owner_headers,
                    json={
                        "model_id": model_id,
                        "user_message": "committed before deadline",
                    },
                )
            )
            await asyncio.wait_for(runtime.entered.wait(), timeout=1)

            async with AsyncSession(test_database_engine) as observer:
                idle_in_transaction = await observer.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() "
                        "AND state = 'idle in transaction'"
                    )
                )
            assert idle_in_transaction == 0

            timed_out = await timed_out_task
            assert timed_out.status_code == 503
            assert timed_out.json()["error"] == {
                "code": "HTTP_ERROR",
                "message": "Local model runtime unavailable",
            }
            assert "0.25" not in timed_out.text
            assert "deadline-model" not in timed_out.text
            assert runtime.active_calls == 0
            assert admission._active_users == set()
            assert admission._active_count == 0

            timed_out_history = await client.get(
                f"/api/v1/conversations/{owner_conversation}/messages",
                headers=owner_headers,
            )
            assert timed_out_history.status_code == 200
            assert [
                (item["role"], item["content"])
                for item in timed_out_history.json()["items"]
            ] == [
                ("user", "owner initial"),
                ("user", "committed before deadline"),
            ]

            runtime.block = False
            app.state.generation_max_duration_seconds = 1.0
            retry = await client.post(
                f"/api/v1/conversations/{owner_conversation}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert retry.status_code == 201
            assert retry.json()["message"]["content"] == "deadline-safe answer"

            other_user_generation = await client.post(
                f"/api/v1/conversations/{other_conversation}/messages/generate",
                headers=other_headers,
                json={"model_id": model_id},
            )
            assert other_user_generation.status_code == 201
            assert admission._active_users == set()
            assert admission._active_count == 0

            cancelled_conversation = await create_conversation(
                client,
                owner_headers,
                "cancellation initial",
            )
            runtime.prepare_blocked_call()
            disconnect_harness = _GenerationDisconnectASGIHarness(
                disconnect_app,
                cancelled_conversation,
                owner_headers["Authorization"],
                {
                    "model_id": model_id,
                    "user_message": "committed before cancellation",
                },
            )
            cancelled_task = asyncio.create_task(
                disconnect_app(
                    disconnect_harness.scope,
                    disconnect_harness.receive,
                    disconnect_harness.send,
                )
            )
            await asyncio.wait_for(runtime.entered.wait(), timeout=1)
            assert disconnect_harness.body_consumed.is_set()

            disconnect_harness.disconnect()
            with pytest.raises(asyncio.CancelledError):
                await cancelled_task

            assert runtime.active_calls == 0
            assert admission._active_users == set()
            assert admission._active_count == 0
            assert disconnect_harness.sent == []
            assert not any(
                task.get_name() == "generation-client-disconnect-watcher"
                and not task.done()
                for task in asyncio.all_tasks()
            )
            async with AsyncSession(test_database_engine) as observer:
                idle_in_transaction = await observer.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() "
                        "AND state = 'idle in transaction'"
                    )
                )
            assert idle_in_transaction == 0
            cancelled_history = await client.get(
                f"/api/v1/conversations/{cancelled_conversation}/messages",
                headers=owner_headers,
            )
            assert cancelled_history.status_code == 200
            assert [
                (item["role"], item["content"])
                for item in cancelled_history.json()["items"]
            ] == [
                ("user", "cancellation initial"),
                ("user", "committed before cancellation"),
            ]

            runtime.block = False
            other_after_disconnect = await create_conversation(
                client,
                other_headers,
                "other after disconnect",
            )
            other_after_disconnect_response = await client.post(
                f"/api/v1/conversations/{other_after_disconnect}/messages/generate",
                headers=other_headers,
                json={"model_id": model_id},
            )
            assert other_after_disconnect_response.status_code == 201

            cancellation_retry = await client.post(
                f"/api/v1/conversations/{cancelled_conversation}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert cancellation_retry.status_code == 201
            assert cancellation_retry.json()["message"]["content"] == (
                "deadline-safe answer"
            )
            assert admission._active_users == set()
            assert admission._active_count == 0

            completed_history = await client.get(
                f"/api/v1/conversations/{cancelled_conversation}/messages",
                headers=owner_headers,
            )
            assert completed_history.status_code == 200
            assert [
                (item["role"], item["content"])
                for item in completed_history.json()["items"]
            ] == [
                ("user", "cancellation initial"),
                ("user", "committed before cancellation"),
                ("assistant", "deadline-safe answer"),
            ]

            original_append = MessageService.append_for_owner
            before_commit_conversation = await create_conversation(
                client,
                owner_headers,
                "assistant cancellation before commit",
            )
            before_commit_entered = asyncio.Event()
            before_commit_blocker = asyncio.Event()

            async def block_before_assistant_commit(
                service,
                owner_id,
                conversation_id,
                role,
                content,
                **kwargs,
            ):
                if role is MessageRole.ASSISTANT:
                    before_commit_entered.set()
                    await before_commit_blocker.wait()
                return await original_append(
                    service,
                    owner_id,
                    conversation_id,
                    role,
                    content,
                    **kwargs,
                )

            with monkeypatch.context() as patch:
                patch.setattr(
                    MessageService,
                    "append_for_owner",
                    block_before_assistant_commit,
                )
                before_commit_harness = _GenerationDisconnectASGIHarness(
                    disconnect_app,
                    before_commit_conversation,
                    owner_headers["Authorization"],
                    {"model_id": model_id},
                )
                before_commit_task = asyncio.create_task(
                    disconnect_app(
                        before_commit_harness.scope,
                        before_commit_harness.receive,
                        before_commit_harness.send,
                    )
                )
                await asyncio.wait_for(before_commit_entered.wait(), timeout=1)
                before_commit_harness.disconnect()
                with pytest.raises(asyncio.CancelledError):
                    await before_commit_task

            assert before_commit_harness.sent == []
            before_commit_history = await client.get(
                f"/api/v1/conversations/{before_commit_conversation}/messages",
                headers=owner_headers,
            )
            assert before_commit_history.status_code == 200
            assert [
                (item["role"], item["content"])
                for item in before_commit_history.json()["items"]
            ] == [
                ("user", "assistant cancellation before commit"),
            ]

            completed_commit_conversation = await create_conversation(
                client,
                owner_headers,
                "assistant commit completion first",
            )
            assistant_committed = asyncio.Event()
            after_commit_blocker = asyncio.Event()

            async def block_after_assistant_commit(
                service,
                owner_id,
                conversation_id,
                role,
                content,
                **kwargs,
            ):
                message = await original_append(
                    service,
                    owner_id,
                    conversation_id,
                    role,
                    content,
                    **kwargs,
                )
                if role is MessageRole.ASSISTANT:
                    assistant_committed.set()
                    await after_commit_blocker.wait()
                return message

            with monkeypatch.context() as patch:
                patch.setattr(
                    MessageService,
                    "append_for_owner",
                    block_after_assistant_commit,
                )
                completed_commit_harness = _GenerationDisconnectASGIHarness(
                    disconnect_app,
                    completed_commit_conversation,
                    owner_headers["Authorization"],
                    {"model_id": model_id},
                )
                completed_commit_task = asyncio.create_task(
                    disconnect_app(
                        completed_commit_harness.scope,
                        completed_commit_harness.receive,
                        completed_commit_harness.send,
                    )
                )
                await asyncio.wait_for(assistant_committed.wait(), timeout=1)
                completed_commit_harness.disconnect()
                with pytest.raises(asyncio.CancelledError):
                    await completed_commit_task

            assert completed_commit_harness.sent == []
            assert admission._active_users == set()
            assert admission._active_count == 0
            completed_commit_history = await client.get(
                f"/api/v1/conversations/{completed_commit_conversation}/messages",
                headers=owner_headers,
            )
            assert completed_commit_history.status_code == 200
            assert [
                (item["role"], item["content"])
                for item in completed_commit_history.json()["items"]
            ] == [
                ("user", "assistant commit completion first"),
                ("assistant", "deadline-safe answer"),
            ]
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory
        if previous_catalog is missing:
            delattr(app.state, "model_catalog")
        else:
            app.state.model_catalog = previous_catalog
        if previous_router is missing:
            delattr(app.state, "text_generation_router")
        else:
            app.state.text_generation_router = previous_router
        if previous_admission is missing:
            delattr(app.state, "generation_admission_controller")
        else:
            app.state.generation_admission_controller = previous_admission
        if previous_duration is missing:
            delattr(app.state, "generation_max_duration_seconds")
        else:
            app.state.generation_max_duration_seconds = previous_duration


@pytest.mark.asyncio
async def test_personal_memory_is_owner_isolated_disableable_and_forgotten(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        other = await UserService(session).create(User())
        owner_id = owner.id
        other_id = other.id
        service = MemoryService(session)
        owned = await service.create_for_owner(
            owner_id,
            MemoryCategory.PROJECT_CONTEXT,
            "The Apollo project deadline is Friday.",
        )
        await service.create_for_owner(
            other_id,
            MemoryCategory.FACT,
            "Foreign private memory.",
        )

        assert [item.id for item in await service.list_for_owner(owner_id)] == [
            owned.id
        ]
        assert await service.forget_for_owner(other_id, owned.id) is None
        retrieved = await service.retrieve_for_owner(
            owner_id,
            "When is the Apollo project deadline?",
        )
        assert [item.id for item in retrieved] == [owned.id]

        disabled = await service.set_enabled_for_owner(owner_id, False)
        assert disabled.enabled is False
        assert await service.retrieve_for_owner(
            owner_id,
            "When is the Apollo project deadline?",
        ) == ()

        forgotten = await service.forget_for_owner(owner_id, owned.id)
        assert forgotten is not None
        assert forgotten.content is None
        assert forgotten.deleted_at is not None

    async with AsyncSession(test_database_engine) as verification_session:
        stored = await verification_session.get(Memory, owned.id)
        assert stored is not None
        assert stored.owner_id == owner_id
        assert stored.content is None
        assert stored.embedding is None
        assert stored.embedding_norm is None
        assert stored.deleted_at is not None
