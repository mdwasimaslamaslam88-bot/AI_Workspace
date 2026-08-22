from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import math
import time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import (
    MAX_TOOL_ARGUMENT_JSON_CHARACTERS,
    MAX_TOOL_RESULT_JSON_CHARACTERS,
    ToolExecution,
    ToolExecutionStatus,
)
from app.repositories.tool import ToolRepository
from app.services.document import DocumentService
from app.services.memory import MemoryService

MAX_CALCULATOR_AST_NODES = 64
MAX_CALCULATOR_DEPTH = 16
MAX_CALCULATOR_ABSOLUTE_VALUE = 1e100
MAX_CALCULATOR_INTEGER_BITS = 256


class ToolNotFoundError(RuntimeError):
    """The requested tool is not in the fixed registry."""


class ToolInputInvalidError(ValueError):
    """Tool arguments do not satisfy the fixed schema."""


class ToolConversationNotFoundError(RuntimeError):
    """The optional conversation is not owned by the current user."""


class _ToolInvocationFailed(RuntimeError):
    pass


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalculatorInput(_StrictInput):
    expression: str = Field(min_length=1, max_length=256, pattern=r"\S")


class LocalTimeInput(_StrictInput):
    timezone: str = Field(
        default="UTC",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*$",
    )


class DocumentSearchInput(_StrictInput):
    query: str = Field(min_length=1, max_length=500, pattern=r"\S")
    limit: StrictInt = Field(default=4, ge=1, le=4)


class ConversationSearchInput(_StrictInput):
    query: str = Field(min_length=1, max_length=500, pattern=r"\S")
    limit: StrictInt = Field(default=10, ge=1, le=10)
    conversation_id: UUID | None = None


class MemorySearchInput(_StrictInput):
    query: str = Field(min_length=1, max_length=500, pattern=r"\S")
    limit: StrictInt = Field(default=8, ge=1, le=8)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[_StrictInput]
    permission: str
    timeout_seconds: float
    max_output_characters: int

    def public_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    id: UUID
    conversation_id: UUID | None
    tool_name: str
    permission: str
    status: ToolExecutionStatus
    initiator: str
    arguments: dict[str, Any]
    result: Any | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None


TOOL_DEFINITIONS = (
    ToolDefinition(
        "calculator",
        "Evaluate bounded arithmetic with no variables, functions, or code execution.",
        CalculatorInput,
        "utility",
        1.0,
        1_024,
    ),
    ToolDefinition(
        "local_time",
        "Return the current time for an installed IANA timezone.",
        LocalTimeInput,
        "utility",
        1.0,
        1_024,
    ),
    ToolDefinition(
        "document_search",
        "Search the current user's ready local document index.",
        DocumentSearchInput,
        "personal_documents_read",
        5.0,
        12_000,
    ),
    ToolDefinition(
        "conversation_search",
        "Search messages only in the current user's conversations.",
        ConversationSearchInput,
        "personal_conversations_read",
        5.0,
        12_000,
    ),
    ToolDefinition(
        "memory_search",
        "Search only the current user's enabled active personal memories.",
        MemorySearchInput,
        "personal_memory_read",
        5.0,
        12_000,
    ),
)
TOOL_REGISTRY = {definition.name: definition for definition in TOOL_DEFINITIONS}


@dataclass(frozen=True, slots=True)
class ValidatedToolCall:
    definition: ToolDefinition
    arguments: dict[str, Any]
    arguments_json: str


def _bounded_number(value: int | float) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolInputInvalidError("calculator expression is invalid")
    if isinstance(value, int):
        if value.bit_length() > MAX_CALCULATOR_INTEGER_BITS:
            raise ToolInputInvalidError("calculator result exceeds its bound")
    elif not math.isfinite(value) or abs(value) > MAX_CALCULATOR_ABSOLUTE_VALUE:
        raise ToolInputInvalidError("calculator result exceeds its bound")
    return value


def _evaluate_arithmetic(expression: str) -> int | float:
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ToolInputInvalidError("calculator expression is invalid") from exc
    if len(tuple(ast.walk(tree))) > MAX_CALCULATOR_AST_NODES:
        raise ToolInputInvalidError("calculator expression is too complex")

    def evaluate(node: ast.AST, depth: int = 0) -> int | float:
        if depth > MAX_CALCULATOR_DEPTH:
            raise ToolInputInvalidError("calculator expression is too complex")
        if isinstance(node, ast.Expression):
            return evaluate(node.body, depth + 1)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)
            ):
                raise ToolInputInvalidError("calculator expression is invalid")
            return _bounded_number(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = evaluate(node.operand, depth + 1)
            value = operand if isinstance(node.op, ast.UAdd) else -operand
            return _bounded_number(value)
        if isinstance(node, ast.BinOp) and isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
        ):
            left = evaluate(node.left, depth + 1)
            right = evaluate(node.right, depth + 1)
            if isinstance(node.op, ast.Pow) and (
                abs(right) > 12 or abs(left) > 1e12
            ):
                raise ToolInputInvalidError("calculator exponent exceeds its bound")
            try:
                if isinstance(node.op, ast.Add):
                    value = left + right
                elif isinstance(node.op, ast.Sub):
                    value = left - right
                elif isinstance(node.op, ast.Mult):
                    value = left * right
                elif isinstance(node.op, ast.Div):
                    value = left / right
                elif isinstance(node.op, ast.FloorDiv):
                    value = left // right
                elif isinstance(node.op, ast.Mod):
                    value = left % right
                else:
                    value = left**right
            except (ArithmeticError, OverflowError) as exc:
                raise ToolInputInvalidError("calculator operation is invalid") from exc
            return _bounded_number(value)
        raise ToolInputInvalidError("calculator expression is invalid")

    return evaluate(tree)


def _canonical_json(value: Any, maximum: int) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ToolInputInvalidError("tool data is invalid") from exc
    if len(encoded) > maximum:
        raise _ToolInvocationFailed("tool data exceeded its bound")
    return encoded


def validate_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
) -> ValidatedToolCall:
    definition = TOOL_REGISTRY.get(tool_name)
    if definition is None:
        raise ToolNotFoundError("tool is not registered")
    try:
        validated = definition.input_model.model_validate(arguments)
    except ValidationError as exc:
        raise ToolInputInvalidError("tool arguments are invalid") from exc
    arguments_value = validated.model_dump(mode="json", exclude_none=True)
    try:
        arguments_json = _canonical_json(
            arguments_value, MAX_TOOL_ARGUMENT_JSON_CHARACTERS
        )
    except _ToolInvocationFailed as exc:
        raise ToolInputInvalidError("tool arguments are invalid") from exc
    return ValidatedToolCall(definition, arguments_value, arguments_json)


def _record(execution: ToolExecution) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        id=execution.id,
        conversation_id=execution.conversation_id,
        tool_name=execution.tool_name,
        permission=execution.permission,
        status=execution.status,
        initiator=execution.initiator,
        arguments=json.loads(execution.arguments_json),
        result=(
            json.loads(execution.result_json)
            if execution.result_json is not None
            else None
        ),
        error_code=execution.error_code,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        duration_ms=execution.duration_ms,
    )


class ToolService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        document_storage=None,
        document_admission: asyncio.Semaphore | None = None,
    ) -> None:
        self.session = session
        self.repository = ToolRepository(session)
        self.document_storage = document_storage
        self.document_admission = document_admission

    @staticmethod
    def definitions() -> tuple[ToolDefinition, ...]:
        return TOOL_DEFINITIONS

    async def list_for_owner(
        self, owner_id: UUID, *, limit: int = 50
    ) -> tuple[ToolExecutionRecord, ...]:
        try:
            executions = await self.repository.list_for_owner(owner_id, limit=limit)
            records = tuple(_record(execution) for execution in executions)
            await self.session.rollback()
            return records
        except BaseException:
            await self.session.rollback()
            raise

    async def execute_for_owner(
        self,
        owner_id: UUID,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        conversation_id: UUID | None = None,
        initiator: str = "explicit_user",
    ) -> ToolExecutionRecord:
        if initiator not in {"explicit_user", "workflow"}:
            raise ValueError("tool initiator is invalid")
        validated_call = validate_tool_call(tool_name, arguments)
        definition = validated_call.definition
        validated = definition.input_model.model_validate(
            validated_call.arguments
        )
        arguments_json = validated_call.arguments_json

        try:
            conversation_is_owned = (
                conversation_id is None
                or await self.repository.conversation_exists_for_owner(
                    owner_id, conversation_id
                )
            )
            if not conversation_is_owned:
                await self.session.rollback()
                raise ToolConversationNotFoundError("conversation not found")
            execution = await self.repository.create_running(
                owner_id,
                conversation_id,
                definition.name,
                definition.permission,
                arguments_json,
                initiator=initiator,
            )
            await self.session.commit()
        except ToolConversationNotFoundError:
            raise
        except BaseException:
            await self.session.rollback()
            raise

        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._invoke(owner_id, definition, validated),
                timeout=definition.timeout_seconds,
            )
            encoded = _canonical_json(result, definition.max_output_characters)
            encoded = _canonical_json(
                json.loads(encoded), MAX_TOOL_RESULT_JSON_CHARACTERS
            )
            return await self._finish(
                owner_id,
                execution.id,
                ToolExecutionStatus.COMPLETED,
                started,
                result_json=encoded,
            )
        except asyncio.TimeoutError:
            return await self._finish(
                owner_id,
                execution.id,
                ToolExecutionStatus.TIMED_OUT,
                started,
                error_code="tool_timed_out",
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._finish(
                    owner_id,
                    execution.id,
                    ToolExecutionStatus.CANCELLED,
                    started,
                    error_code="tool_cancelled",
                )
            )
            raise
        except (ToolInputInvalidError, _ToolInvocationFailed, ZoneInfoNotFoundError):
            return await self._finish(
                owner_id,
                execution.id,
                ToolExecutionStatus.FAILED,
                started,
                error_code="tool_execution_failed",
            )
        except BaseException:
            await self.session.rollback()
            return await self._finish(
                owner_id,
                execution.id,
                ToolExecutionStatus.FAILED,
                started,
                error_code="tool_unavailable",
            )

    async def _finish(
        self,
        owner_id: UUID,
        execution_id: UUID,
        status: ToolExecutionStatus,
        started: float,
        *,
        result_json: str | None = None,
        error_code: str | None = None,
    ) -> ToolExecutionRecord:
        duration_ms = max(0, int((time.monotonic() - started) * 1_000))
        try:
            await self.session.rollback()
            execution = await self.repository.finish(
                owner_id,
                execution_id,
                status,
                duration_ms,
                result_json=result_json,
                error_code=error_code,
            )
            if execution is None:
                raise RuntimeError("tool execution terminal state was lost")
            await self.session.commit()
            return _record(execution)
        except BaseException:
            await self.session.rollback()
            raise

    async def _invoke(
        self,
        owner_id: UUID,
        definition: ToolDefinition,
        validated: _StrictInput,
    ) -> Any:
        if definition.name == "calculator":
            assert isinstance(validated, CalculatorInput)
            return {"value": _evaluate_arithmetic(validated.expression)}
        if definition.name == "local_time":
            assert isinstance(validated, LocalTimeInput)
            zone = ZoneInfo(validated.timezone)
            current = datetime.now(zone)
            return {
                "timezone": validated.timezone,
                "iso8601": current.isoformat(),
                "utc_offset": current.strftime("%z"),
            }
        if definition.name == "document_search":
            assert isinstance(validated, DocumentSearchInput)
            items = await DocumentService(
                self.session,
                self.document_storage,
                self.document_admission,
            ).search_for_owner(owner_id, validated.query, limit=validated.limit)
            return {
                "items": [
                    {
                        "asset_id": str(item.asset_id),
                        "content": item.content,
                        "score": round(item.score, 6),
                        "original_filename": item.original_filename,
                        "page_number": item.page_number,
                        "row_start": item.row_start,
                        "row_end": item.row_end,
                        "section": item.section,
                    }
                    for item in items
                ]
            }
        if definition.name == "memory_search":
            assert isinstance(validated, MemorySearchInput)
            items = await MemoryService(self.session).retrieve_for_owner(
                owner_id, validated.query, limit=validated.limit
            )
            return {
                "items": [
                    {
                        "id": str(item.id),
                        "category": item.category.value,
                        "content": item.content,
                        "score": round(item.score, 6),
                        "created_at": item.created_at.isoformat(),
                    }
                    for item in items
                ]
            }
        if definition.name == "conversation_search":
            assert isinstance(validated, ConversationSearchInput)
            conversation_is_owned = (
                validated.conversation_id is None
                or await self.repository.conversation_exists_for_owner(
                    owner_id, validated.conversation_id
                )
            )
            if not conversation_is_owned:
                raise _ToolInvocationFailed("conversation is unavailable")
            rows = await self.repository.search_conversations_for_owner(
                owner_id,
                validated.query,
                limit=validated.limit,
                conversation_id=validated.conversation_id,
            )
            await self.session.rollback()
            lowered = validated.query.lower()
            items = []
            for message_id, found_conversation_id, title, sequence, role, content in rows:
                match = content.lower().find(lowered)
                start = max(0, match - 120)
                items.append(
                    {
                        "message_id": str(message_id),
                        "conversation_id": str(found_conversation_id),
                        "conversation_title": title,
                        "sequence_number": sequence,
                        "role": role,
                        "excerpt": content[start : start + 500],
                    }
                )
            return {"items": items}
        raise ToolNotFoundError("tool is not registered")


async def reconcile_tool_executions(session_factory) -> int:
    """Close tool calls interrupted by a previous local process lifetime."""
    if session_factory is None:
        return 0
    async with session_factory() as session:
        repository = ToolRepository(session)
        try:
            count = await repository.reconcile_interrupted()
            await session.commit()
            return count
        except BaseException:
            await session.rollback()
            raise
