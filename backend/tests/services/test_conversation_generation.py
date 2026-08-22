import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.conversation_generation as generation_module
from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelDescriptor,
    ModelModality,
    ResolvedModel,
)
from app.ai.generation import (
    TextGenerationRequestTooLargeError,
    TextGenerationResult,
    TextGenerationRuntimeUnavailableError,
    TextGenerationRuntimeUnsupportedError,
)
from app.models import Conversation, Message, MessageRole
from app.models.message import (
    MAX_MESSAGE_CONTENT_CHARACTERS,
    MessageContentTooLargeError,
)
from app.repositories.message import (
    GenerationContextMessage,
    GenerationContextSnapshot,
    MessageAttachmentMetadata,
)
from app.services.conversation_generation import (
    MAX_GENERATION_CONTEXT_CHARACTERS,
    MAX_GENERATION_CONTEXT_MESSAGES,
    MAX_GENERATION_OUTPUT_TOKENS,
    MAX_GENERATION_MIN_P,
    MAX_GENERATION_REPEAT_PENALTY,
    MAX_GENERATION_REPEAT_LAST_N,
    MAX_GENERATION_TYPICAL_P,
    MAX_GENERATION_PRESENCE_PENALTY,
    MAX_GENERATION_FREQUENCY_PENALTY,
    MAX_GENERATION_STOP_SEQUENCE_CHARACTERS,
    MAX_GENERATION_STOP_SEQUENCES,
    MAX_GENERATION_SEED,
    MAX_GENERATION_TEMPERATURE,
    MAX_GENERATION_TOP_K,
    MAX_GENERATION_TOP_P,
    MIN_GENERATION_PRESENCE_PENALTY,
    MIN_GENERATION_FREQUENCY_PENALTY,
    MIN_GENERATION_REPEAT_PENALTY,
    ConversationChangedDuringGenerationError,
    ConversationGenerationContextTooLargeError,
    ConversationGenerationModelNotFoundError,
    ConversationGenerationModelUnavailableError,
    ConversationGenerationNotFoundError,
    ConversationGenerationNotReadyError,
    ConversationGenerationService,
    ConversationGenerationVisionCapabilityError,
)
from app.services.generation_admission import (
    GenerationAdmissionController,
    GenerationAdmissionRejectedError,
)
from app.services.vision_input import (
    VisionInputContentUnavailableError,
    VisionInputTooLargeError,
    VisionInputUnsupportedError,
)


MODEL_ID = f"local-runtime:{'a' * 24}"


def _conversation(owner_id, conversation_id, next_sequence: int) -> Conversation:
    return Conversation(
        id=conversation_id,
        owner_id=owner_id,
        title=None,
        next_message_sequence=next_sequence,
        created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )


def _message(conversation_id, role, content: str, sequence: int) -> Message:
    return Message(
        id=uuid4(),
        conversation_id=conversation_id,
        role=role,
        content=content,
        sequence_number=sequence,
        created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )


def _resolved(
    availability: ModelAvailability = ModelAvailability.AVAILABLE,
    capabilities: tuple[ModelCapability, ...] = (
        ModelCapability.TEXT_GENERATION,
    ),
) -> ResolvedModel:
    return ResolvedModel(
        descriptor=ModelDescriptor(
            model_id=MODEL_ID,
            display_name="Local model",
            runtime_id="local-runtime",
            modality=ModelModality.TEXT,
            family=None,
            parameter_class="70B+",
            capabilities=capabilities,
            context_window=None,
            quantization=None,
            estimated_vram_bytes=None,
            availability=availability,
        ),
        runtime_reference="private-runtime-reference",
    )


def _context_snapshot(
    context,
    *,
    candidate_count: int | None = None,
    final_sequence_number: int | None = None,
    oversized: bool = False,
) -> GenerationContextSnapshot:
    if isinstance(context, GenerationContextSnapshot):
        return context
    messages = tuple(
        GenerationContextMessage(
            role=message.role,
            content=message.content,
            sequence_number=message.sequence_number,
        )
        for message in context
    )
    return GenerationContextSnapshot(
        messages=messages,
        candidate_count=(
            len(messages) if candidate_count is None else candidate_count
        ),
        final_sequence_number=(
            messages[-1].sequence_number
            if final_sequence_number is None and messages
            else final_sequence_number
        ),
        oversized=oversized,
    )


def _dependencies(
    monkeypatch,
    *,
    conversation,
    context,
    appended,
    generated=TextGenerationResult(content="  exact answer  "),
):
    get_for_owner = AsyncMock(return_value=conversation)
    conversation_factory = Mock(
        return_value=Mock(get_for_owner=get_for_owner)
    )
    context_for_owner = AsyncMock(return_value=_context_snapshot(context))
    append_for_owner = AsyncMock(return_value=appended)
    message_factory = Mock(
        return_value=Mock(
            list_generation_context_for_owner=context_for_owner,
            append_for_owner=append_for_owner,
        )
    )
    catalog = Mock(resolve_model=AsyncMock(return_value=_resolved()))
    router = Mock(generate=AsyncMock(return_value=generated))
    admission = GenerationAdmissionController(1)
    monkeypatch.setattr(
        generation_module,
        "ConversationService",
        conversation_factory,
    )
    monkeypatch.setattr(
        generation_module,
        "MessageService",
        message_factory,
    )
    return {
        "conversation_factory": conversation_factory,
        "get": get_for_owner,
        "message_factory": message_factory,
        "context": context_for_owner,
        "append": append_for_owner,
        "catalog": catalog,
        "router": router,
        "admission": admission,
    }


@pytest.mark.asyncio
async def test_generation_releases_read_transaction_before_local_inference(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    events: list[str] = []
    messages = (
        _message(conversation_id, MessageRole.SYSTEM, "  system prompt  ", 1),
        _message(conversation_id, MessageRole.USER, "question 1", 2),
        _message(conversation_id, MessageRole.ASSISTANT, "answer 1", 3),
        _message(conversation_id, MessageRole.USER, "  question 2  ", 4),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "  exact answer  ",
        5,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 5),
        context=messages,
        appended=appended,
    )
    dependencies["get"].side_effect = lambda *_args: (
        events.append("get") or _conversation(owner_id, conversation_id, 5)
    )
    dependencies["context"].side_effect = lambda *_args, **_kwargs: (
        events.append("context") or _context_snapshot(messages)
    )

    async def rollback():
        events.append("rollback")

    async def resolve(_model_id):
        events.append("resolve")
        return _resolved()

    async def generate(*_args, **_kwargs):
        events.append("generate")
        return TextGenerationResult(content="  exact answer  ")

    async def append(*_args, **_kwargs):
        events.append("append")
        return appended

    session.rollback.side_effect = rollback
    dependencies["catalog"].resolve_model.side_effect = resolve
    dependencies["router"].generate.side_effect = generate
    dependencies["append"].side_effect = append

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert result is appended
    assert events == ["get", "context", "rollback", "resolve", "generate", "append"]
    dependencies["conversation_factory"].assert_called_once_with(session)
    dependencies["get"].assert_awaited_once_with(owner_id, conversation_id)
    dependencies["message_factory"].assert_has_calls([call(session), call(session)])
    dependencies["context"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        max_messages=MAX_GENERATION_CONTEXT_MESSAGES,
        max_context_characters=MAX_GENERATION_CONTEXT_CHARACTERS,
    )
    generated_messages = dependencies["router"].generate.await_args.args[1]
    assert [(message.role.value, message.content) for message in generated_messages] == [
        ("system", "  system prompt  "),
        ("user", "question 1"),
        ("assistant", "answer 1"),
        ("user", "  question 2  "),
    ]
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        generated_messages,
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
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
    )
    dependencies["append"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "  exact answer  ",
        expected_sequence_number=5,
    )
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_output_tokens", [1, 128, 1024])
async def test_generation_forwards_exact_valid_output_bound(
    monkeypatch,
    max_output_tokens,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        max_output_tokens=max_output_tokens,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=max_output_tokens,
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
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "max_output_tokens",
    [None, True, False, "128", 128.0, 0, -1, 1025],
)
async def test_generation_rejects_invalid_output_bound_before_side_effects(
    monkeypatch,
    max_output_tokens,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            max_output_tokens=max_output_tokens,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [0, 1, 2, 0.5, 2.0])
async def test_generation_forwards_exact_valid_temperature(
    monkeypatch,
    temperature,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        temperature=temperature,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=temperature,
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
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [0, 42, MAX_GENERATION_SEED])
async def test_generation_forwards_exact_valid_seed(
    monkeypatch,
    seed,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        seed=seed,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=None,
        seed=seed,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
        repeat_last_n=None,
        typical_p=None,
        presence_penalty=None,
        frequency_penalty=None,
        stop_sequences=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("top_p", [0, 1, 0.5, 0.9, MAX_GENERATION_TOP_P])
async def test_generation_forwards_exact_valid_top_p(
    monkeypatch,
    top_p,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        top_p=top_p,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=None,
        seed=None,
        top_p=top_p,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
        repeat_last_n=None,
        typical_p=None,
        presence_penalty=None,
        frequency_penalty=None,
        stop_sequences=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("top_k", [1, 40, MAX_GENERATION_TOP_K])
async def test_generation_forwards_exact_valid_top_k(
    monkeypatch,
    top_k,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        top_k=top_k,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=top_k,
        min_p=None,
        repeat_penalty=None,
        repeat_last_n=None,
        typical_p=None,
        presence_penalty=None,
        frequency_penalty=None,
        stop_sequences=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("min_p", [0, 1, 0.05, 0.5, MAX_GENERATION_MIN_P])
async def test_generation_forwards_exact_valid_min_p(
    monkeypatch,
    min_p,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        min_p=min_p,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=min_p,
        repeat_penalty=None,
        repeat_last_n=None,
        typical_p=None,
        presence_penalty=None,
        frequency_penalty=None,
        stop_sequences=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repeat_penalty",
    [
        MIN_GENERATION_REPEAT_PENALTY,
        0.9,
        1,
        1.1,
        1.5,
        MAX_GENERATION_REPEAT_PENALTY,
    ],
)
async def test_generation_forwards_exact_valid_repeat_penalty(
    monkeypatch,
    repeat_penalty,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        repeat_penalty=repeat_penalty,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=repeat_penalty,
        repeat_last_n=None,
        typical_p=None,
        presence_penalty=None,
        frequency_penalty=None,
        stop_sequences=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repeat_last_n",
    [0, 1, 64, MAX_GENERATION_REPEAT_LAST_N],
)
async def test_generation_forwards_exact_valid_repeat_last_n(
    monkeypatch,
    repeat_last_n,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        repeat_last_n=repeat_last_n,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
        repeat_last_n=repeat_last_n,
        typical_p=None,
        presence_penalty=None,
        frequency_penalty=None,
        stop_sequences=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "typical_p",
    [0, 1, 0.05, 0.7, MAX_GENERATION_TYPICAL_P],
)
async def test_generation_forwards_exact_valid_typical_p(
    monkeypatch,
    typical_p,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        typical_p=typical_p,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
        repeat_last_n=None,
        typical_p=typical_p,
        presence_penalty=None,
        frequency_penalty=None,
        stop_sequences=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "presence_penalty",
    [
        MIN_GENERATION_PRESENCE_PENALTY,
        -1,
        0,
        0.5,
        1,
        1.5,
        MAX_GENERATION_PRESENCE_PENALTY,
    ],
)
async def test_generation_forwards_exact_valid_presence_penalty(
    monkeypatch,
    presence_penalty,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        presence_penalty=presence_penalty,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
        repeat_last_n=None,
        typical_p=None,
        presence_penalty=presence_penalty,
        frequency_penalty=None,
        stop_sequences=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frequency_penalty",
    [
        MIN_GENERATION_FREQUENCY_PENALTY,
        -1,
        0,
        0.5,
        1,
        1.5,
        MAX_GENERATION_FREQUENCY_PENALTY,
    ],
)
async def test_generation_forwards_exact_valid_frequency_penalty(
    monkeypatch,
    frequency_penalty,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        frequency_penalty=frequency_penalty,
    )

    assert result is appended
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
        repeat_last_n=None,
        typical_p=None,
        presence_penalty=None,
        frequency_penalty=frequency_penalty,
        stop_sequences=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop_sequences",
    [
        ["END"],
        ["\n", "\t", "\n", "\u0000"],
        ["界" * MAX_GENERATION_STOP_SEQUENCE_CHARACTERS],
    ],
)
async def test_generation_forwards_exact_valid_stop_sequences(
    monkeypatch,
    stop_sequences,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = (
        _message(conversation_id, MessageRole.USER, "question", 1),
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=messages,
        appended=appended,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        stop_sequences=stop_sequences,
    )

    assert result is appended
    assert (
        dependencies["router"].generate.await_args.kwargs["stop_sequences"]
        is stop_sequences
    )
    dependencies["router"].generate.assert_awaited_once_with(
        _resolved(),
        dependencies["router"].generate.await_args.args[1],
        max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
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
        stop_sequences=stop_sequences,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "temperature",
    [
        True,
        False,
        "0.5",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        MAX_GENERATION_TEMPERATURE + 0.01,
        10**1000,
    ],
)
async def test_generation_rejects_invalid_temperature_before_side_effects(
    monkeypatch,
    temperature,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            temperature=temperature,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seed",
    [
        True,
        False,
        "42",
        42.0,
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -1,
        MAX_GENERATION_SEED + 1,
    ],
)
async def test_generation_rejects_invalid_seed_before_side_effects(
    monkeypatch,
    seed,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            seed=seed,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "top_p",
    [
        True,
        False,
        "0.9",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        MAX_GENERATION_TOP_P + 0.01,
        10**1000,
    ],
)
async def test_generation_rejects_invalid_top_p_before_side_effects(
    monkeypatch,
    top_p,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            top_p=top_p,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "top_k",
    [
        True,
        False,
        "40",
        40.0,
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        0,
        -1,
        MAX_GENERATION_TOP_K + 1,
    ],
)
async def test_generation_rejects_invalid_top_k_before_side_effects(
    monkeypatch,
    top_k,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            top_k=top_k,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "min_p",
    [
        True,
        False,
        "0.05",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        MAX_GENERATION_MIN_P + 0.01,
        10**1000,
    ],
)
async def test_generation_rejects_invalid_min_p_before_side_effects(
    monkeypatch,
    min_p,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            min_p=min_p,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repeat_penalty",
    [
        True,
        False,
        "1.1",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        MIN_GENERATION_REPEAT_PENALTY - 0.01,
        MAX_GENERATION_REPEAT_PENALTY + 0.01,
        10**1000,
    ],
)
async def test_generation_rejects_invalid_repeat_penalty_before_side_effects(
    monkeypatch,
    repeat_penalty,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            repeat_penalty=repeat_penalty,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repeat_last_n",
    [
        True,
        False,
        "64",
        64.0,
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -1,
        -2,
        MAX_GENERATION_REPEAT_LAST_N + 1,
    ],
)
async def test_generation_rejects_invalid_repeat_last_n_before_side_effects(
    monkeypatch,
    repeat_last_n,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            repeat_last_n=repeat_last_n,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "typical_p",
    [
        True,
        False,
        "0.7",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        MAX_GENERATION_TYPICAL_P + 0.01,
        10**1000,
    ],
)
async def test_generation_rejects_invalid_typical_p_before_side_effects(
    monkeypatch,
    typical_p,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            typical_p=typical_p,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "presence_penalty",
    [
        True,
        False,
        "1.5",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        MIN_GENERATION_PRESENCE_PENALTY - 0.01,
        MAX_GENERATION_PRESENCE_PENALTY + 0.01,
        10**1000,
    ],
)
async def test_generation_rejects_invalid_presence_penalty_before_side_effects(
    monkeypatch,
    presence_penalty,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            presence_penalty=presence_penalty,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frequency_penalty",
    [
        True,
        False,
        "1.5",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        MIN_GENERATION_FREQUENCY_PENALTY - 0.01,
        MAX_GENERATION_FREQUENCY_PENALTY + 0.01,
        10**1000,
    ],
)
async def test_generation_rejects_invalid_frequency_penalty_before_side_effects(
    monkeypatch,
    frequency_penalty,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            frequency_penalty=frequency_penalty,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop_sequences",
    [
        "END",
        True,
        1,
        1.0,
        {},
        (),
        [],
        ["a", "b", "c", "d", "e"],
        [None],
        [True],
        [1],
        [1.0],
        [[]],
        [{}],
        [""],
        ["x" * (MAX_GENERATION_STOP_SEQUENCE_CHARACTERS + 1)],
    ],
)
async def test_generation_rejects_invalid_stop_sequences_before_side_effects(
    monkeypatch,
    stop_sequences,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="must not persist",
            stop_sequences=stop_sequences,
        )

    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["context"].assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_appends_exact_user_message_before_context_and_inference(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    events: list[str] = []
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "  exact follow-up  ",
        4,
    )
    appended_assistant = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "  exact answer  ",
        5,
    )
    messages = (
        _message(conversation_id, MessageRole.SYSTEM, "system", 1),
        _message(conversation_id, MessageRole.USER, "question", 2),
        _message(conversation_id, MessageRole.ASSISTANT, "answer", 3),
        appended_user,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 4),
        context=messages,
        appended=appended_assistant,
    )
    dependencies["get"].side_effect = lambda *_args: (
        events.append("get") or _conversation(owner_id, conversation_id, 4)
    )

    async def append(*args, **_kwargs):
        if args[2] is MessageRole.USER:
            events.append("append_user")
            return appended_user
        events.append("append_assistant")
        return appended_assistant

    dependencies["append"].side_effect = append
    dependencies["context"].side_effect = lambda *_args, **_kwargs: (
        events.append("context") or _context_snapshot(messages)
    )

    async def rollback():
        events.append("rollback")

    async def resolve(_model_id):
        events.append("resolve")
        return _resolved()

    async def generate(*_args, **_kwargs):
        events.append("generate")
        return TextGenerationResult(content="  exact answer  ")

    session.rollback.side_effect = rollback
    dependencies["catalog"].resolve_model.side_effect = resolve
    dependencies["router"].generate.side_effect = generate

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        user_message="  exact follow-up  ",
    )

    assert result is appended_assistant
    assert events == [
        "get",
        "append_user",
        "context",
        "rollback",
        "resolve",
        "generate",
        "append_assistant",
    ]
    dependencies["message_factory"].assert_has_calls(
        [call(session), call(session), call(session)]
    )
    assert dependencies["append"].await_args_list == [
        call(
            owner_id,
            conversation_id,
            MessageRole.USER,
            "  exact follow-up  ",
        ),
        call(
            owner_id,
            conversation_id,
            MessageRole.ASSISTANT,
            "  exact answer  ",
            expected_sequence_number=5,
        ),
    ]
    generated_messages = dependencies["router"].generate.await_args.args[1]
    assert [(message.role.value, message.content) for message in generated_messages] == [
        ("system", "system"),
        ("user", "question"),
        ("assistant", "answer"),
        ("user", "  exact follow-up  "),
    ]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_generation_user_message_stops_before_admission(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(),
        appended=None,
    )
    dependencies["admission"].admit = Mock(
        side_effect=AssertionError("admission must not be acquired")
    )

    with pytest.raises(MessageContentTooLargeError) as captured:
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="x" * (MAX_MESSAGE_CONTENT_CHARACTERS + 1),
        )

    assert str(captured.value) == "persisted text is too large"
    dependencies["admission"].admit.assert_not_called()
    dependencies["conversation_factory"].assert_not_called()
    dependencies["get"].assert_not_awaited()
    dependencies["message_factory"].assert_not_called()
    dependencies["append"].assert_not_awaited()
    dependencies["context"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_generated_assistant_at_exact_character_boundary_persists(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    content = "é" * MAX_MESSAGE_CONTENT_CHARACTERS
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        content,
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "question", 1),
        ),
        appended=appended,
        generated=TextGenerationResult(content=content),
    )

    result = await ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert result is appended
    dependencies["append"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        content,
        expected_sequence_number=2,
    )


@pytest.mark.asyncio
async def test_oversized_assistant_preserves_committed_user_and_allows_retry(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "committed user",
        2,
    )
    appended_assistant = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        3,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "initial", 1),
            appended_user,
        ),
        appended=None,
        generated=TextGenerationResult(
            content="x" * (MAX_MESSAGE_CONTENT_CHARACTERS + 1)
        ),
    )

    async def append(_owner, _conversation, role, _content, **_kwargs):
        if role is MessageRole.USER:
            return appended_user
        return appended_assistant

    dependencies["append"].side_effect = append
    service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )

    with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
        await service.generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="committed user",
        )

    assert str(captured.value) == "local text generation is unavailable"
    assert dependencies["append"].await_args_list == [
        call(
            owner_id,
            conversation_id,
            MessageRole.USER,
            "committed user",
        )
    ]
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0

    dependencies["get"].return_value = _conversation(
        owner_id,
        conversation_id,
        3,
    )
    dependencies["router"].generate.return_value = TextGenerationResult(
        content="answer"
    )
    result = await service.generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
    )

    assert result is appended_assistant
    assert dependencies["append"].await_args_list[-1] == call(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        expected_sequence_number=3,
    )
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0


@pytest.mark.asyncio
async def test_combined_generation_owner_miss_stops_before_user_append_or_runtime(
    monkeypatch,
):
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=None,
        context=(),
        appended=None,
    )

    with pytest.raises(ConversationGenerationNotFoundError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            uuid4(),
            uuid4(),
            MODEL_ID,
            user_message="must not append",
        )

    dependencies["append"].assert_not_awaited()
    dependencies["context"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_combined_generation_append_miss_stops_before_context_or_runtime(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(),
        appended=None,
    )

    with pytest.raises(ConversationGenerationNotFoundError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="exact user content",
        )

    dependencies["append"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.USER,
        "exact user content",
    )
    dependencies["context"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_winning_before_context_capture_stops_before_discovery(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "request user",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=GenerationContextSnapshot(
            messages=(),
            candidate_count=3,
            final_sequence_number=3,
            oversized=True,
        ),
        appended=appended_user,
    )

    with pytest.raises(ConversationChangedDuringGenerationError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="request user",
        )

    dependencies["append"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.USER,
        "request user",
    )
    session.rollback.assert_awaited_once_with()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["model", "runtime"])
async def test_failure_after_combined_user_append_never_appends_assistant(
    monkeypatch,
    failure,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "committed user content",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "existing", 1),
            appended_user,
        ),
        appended=appended_user,
    )
    if failure == "model":
        dependencies["catalog"].resolve_model.return_value = None
        expected_error = ConversationGenerationModelNotFoundError
    else:
        dependencies["router"].generate.side_effect = (
            TextGenerationRuntimeUnavailableError("runtime unavailable")
        )
        expected_error = TextGenerationRuntimeUnavailableError

    with pytest.raises(expected_error):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="committed user content",
        )

    dependencies["append"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.USER,
        "committed user content",
    )


@pytest.mark.asyncio
async def test_missing_or_foreign_conversation_stops_before_context_or_runtime(
    monkeypatch,
):
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=None,
        context=(),
        appended=None,
    )

    with pytest.raises(ConversationGenerationNotFoundError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(uuid4(), uuid4(), MODEL_ID)

    session.rollback.assert_awaited_once_with()
    dependencies["context"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    dependencies["append"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "assistant", "tool"])
async def test_invalid_conversation_state_stops_before_discovery(
    monkeypatch,
    case,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    items = ()
    next_sequence = 1
    if case == "assistant":
        items = (
            _message(conversation_id, MessageRole.ASSISTANT, "answer", 1),
        )
        next_sequence = 2
    elif case == "tool":
        items = (_message(conversation_id, MessageRole.TOOL, "result", 1),)
        next_sequence = 2
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(
            owner_id,
            conversation_id,
            next_sequence,
        ),
        context=items,
        appended=None,
    )

    with pytest.raises(ConversationGenerationNotReadyError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    session.rollback.assert_awaited_once_with()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit_case", ["count", "characters"])
async def test_context_bounds_stop_before_discovery(monkeypatch, limit_case):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    if limit_case == "count":
        context = GenerationContextSnapshot(
            messages=(),
            candidate_count=MAX_GENERATION_CONTEXT_MESSAGES + 1,
            final_sequence_number=MAX_GENERATION_CONTEXT_MESSAGES + 1,
            oversized=True,
        )
        next_sequence = MAX_GENERATION_CONTEXT_MESSAGES + 2
    else:
        context = GenerationContextSnapshot(
            messages=(),
            candidate_count=2,
            final_sequence_number=2,
            oversized=True,
        )
        next_sequence = 3
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, next_sequence),
        context=context,
        appended=None,
    )

    with pytest.raises(ConversationGenerationContextTooLargeError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    session.rollback.assert_awaited_once_with()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    dependencies["append"].assert_not_awaited()
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0


@pytest.mark.asyncio
async def test_exact_generation_context_limits_reach_runtime_unchanged(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    messages = tuple(
        _message(
            conversation_id,
            MessageRole.USER,
            "🧠" * 99_901 if sequence == 100 else "界",
            sequence,
        )
        for sequence in range(1, MAX_GENERATION_CONTEXT_MESSAGES + 1)
    )
    appended_assistant = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        101,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 101),
        context=messages,
        appended=appended_assistant,
    )

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert result is appended_assistant
    generated_context = dependencies["router"].generate.await_args.args[1]
    assert len(generated_context) == MAX_GENERATION_CONTEXT_MESSAGES
    assert sum(len(item.content) for item in generated_context) == (
        MAX_GENERATION_CONTEXT_CHARACTERS
    )
    assert [item.content for item in generated_context] == [
        message.content for message in messages
    ]
    assert generated_context[-1].content == "🧠" * 99_901


@pytest.mark.asyncio
async def test_context_rejection_preserves_committed_user_and_allows_retry(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    initial = _message(conversation_id, MessageRole.USER, "initial", 1)
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "committed user",
        2,
    )
    appended_assistant = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        3,
    )
    oversized = GenerationContextSnapshot(
        messages=(),
        candidate_count=2,
        final_sequence_number=2,
        oversized=True,
    )
    valid_retry = _context_snapshot((initial, appended_user))
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=oversized,
        appended=None,
    )
    dependencies["context"].side_effect = [oversized, valid_retry]

    async def append(_owner, _conversation_id, role, _content, **_kwargs):
        if role is MessageRole.USER:
            return appended_user
        return appended_assistant

    dependencies["append"].side_effect = append
    service = ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )

    with pytest.raises(ConversationGenerationContextTooLargeError):
        await service.generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="committed user",
        )

    assert dependencies["append"].await_args_list == [
        call(
            owner_id,
            conversation_id,
            MessageRole.USER,
            "committed user",
        )
    ]
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0

    dependencies["get"].return_value = _conversation(
        owner_id,
        conversation_id,
        3,
    )
    result = await service.generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
    )

    assert result is appended_assistant
    assert dependencies["append"].await_args_list[-1] == call(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "  exact answer  ",
        expected_sequence_number=3,
    )


@pytest.mark.asyncio
async def test_context_query_cancellation_releases_admission_and_allows_retry(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    valid = _context_snapshot(
        (_message(conversation_id, MessageRole.USER, "question", 1),)
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=valid,
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )
    dependencies["context"].side_effect = [asyncio.CancelledError, valid]
    service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )

    with pytest.raises(asyncio.CancelledError):
        await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()

    result = await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)
    assert result.role is MessageRole.ASSISTANT


@pytest.mark.asyncio
async def test_inconsistent_snapshot_stops_before_inference(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 3),
        context=(
            _message(conversation_id, MessageRole.USER, "question", 1),
        ),
        appended=None,
    )

    with pytest.raises(ConversationChangedDuringGenerationError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_model_does_not_invoke_generation_or_append(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "question", 1),
        ),
        appended=None,
    )
    dependencies["catalog"].resolve_model.return_value = None

    with pytest.raises(ConversationGenerationModelNotFoundError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    dependencies["router"].generate.assert_not_awaited()
    dependencies["append"].assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_model_stops_before_inference_or_append(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "question", 1),
        ),
        appended=None,
    )
    dependencies["catalog"].resolve_model.return_value = _resolved(
        ModelAvailability.UNAVAILABLE
    )

    with pytest.raises(ConversationGenerationModelUnavailableError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    dependencies["router"].generate.assert_not_awaited()
    dependencies["append"].assert_not_awaited()


@pytest.mark.asyncio
async def test_non_text_model_preserves_committed_user_and_skips_router(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "follow-up",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "question", 1),
            appended_user,
        ),
        appended=appended_user,
    )
    dependencies["catalog"].resolve_model.return_value = _resolved(
        capabilities=(ModelCapability.EMBEDDINGS,)
    )

    with pytest.raises(
        TextGenerationRuntimeUnsupportedError,
        match="does not support text generation",
    ):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="follow-up",
        )

    dependencies["append"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.USER,
        "follow-up",
    )
    dependencies["router"].generate.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_incomplete_runtime_response_is_never_persisted(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "question", 1),
        ),
        appended=None,
    )
    dependencies["router"].generate.side_effect = (
        TextGenerationRuntimeUnavailableError(
            "local text runtime returned an invalid response"
        )
    )

    with pytest.raises(TextGenerationRuntimeUnavailableError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    dependencies["router"].generate.assert_awaited_once()
    dependencies["append"].assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_conversation_rejects_stale_generated_output(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "question", 1),
        ),
        appended=None,
    )

    with pytest.raises(ConversationChangedDuringGenerationError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    dependencies["router"].generate.assert_awaited_once()
    dependencies["append"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "  exact answer  ",
        expected_sequence_number=2,
    )


@pytest.mark.asyncio
async def test_admission_denial_happens_after_owner_lookup_before_side_effects(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )
    service = ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )

    async with dependencies["admission"].admit(owner_id):
        with pytest.raises(GenerationAdmissionRejectedError):
            await service.generate_for_owner(
                owner_id,
                conversation_id,
                MODEL_ID,
                user_message="must not persist",
            )

    dependencies["get"].assert_awaited_once_with(owner_id, conversation_id)
    dependencies["append"].assert_not_awaited()
    dependencies["context"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0

    result = await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)
    assert result.role is MessageRole.ASSISTANT


@pytest.mark.asyncio
@pytest.mark.parametrize("different_conversation", [False, True])
async def test_simultaneous_same_user_generation_invokes_runtime_once(
    monkeypatch,
    different_conversation,
):
    owner_id = uuid4()
    first_conversation_id = uuid4()
    second_conversation_id = (
        uuid4() if different_conversation else first_conversation_id
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=None,
        context=(),
        appended=None,
    )
    dependencies["get"].side_effect = (
        lambda requested_owner, requested_conversation: _conversation(
            requested_owner,
            requested_conversation,
            2,
        )
    )
    dependencies["context"].side_effect = (
        lambda _owner, requested_conversation, **_kwargs: _context_snapshot(
            (
                _message(
                    requested_conversation,
                    MessageRole.USER,
                    "question",
                    1,
                ),
            )
        )
    )

    async def append(
        _owner,
        requested_conversation,
        role,
        content,
        **_kwargs,
    ):
        return _message(requested_conversation, role, content, 2)

    dependencies["append"].side_effect = append
    runtime_entered = asyncio.Event()
    release_runtime = asyncio.Event()

    async def generate(*_args, **_kwargs):
        runtime_entered.set()
        await release_runtime.wait()
        return TextGenerationResult(content="answer")

    dependencies["router"].generate.side_effect = generate
    first_service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )
    second_service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )

    first = asyncio.create_task(
        first_service.generate_for_owner(
            owner_id,
            first_conversation_id,
            MODEL_ID,
        )
    )
    await runtime_entered.wait()

    with pytest.raises(GenerationAdmissionRejectedError):
        await second_service.generate_for_owner(
            owner_id,
            second_conversation_id,
            MODEL_ID,
            user_message="rejected user message",
        )

    assert dependencies["router"].generate.await_count == 1
    dependencies["append"].assert_not_awaited()
    release_runtime.set()
    result = await first

    assert result.role is MessageRole.ASSISTANT
    assert dependencies["router"].generate.await_count == 1
    assert dependencies["append"].await_count == 1
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0


@pytest.mark.asyncio
async def test_different_users_never_exceed_global_runtime_cap(monkeypatch):
    users = [uuid4(), uuid4(), uuid4()]
    conversations = [uuid4(), uuid4(), uuid4()]
    dependencies = _dependencies(
        monkeypatch,
        conversation=None,
        context=(),
        appended=None,
    )
    admission = GenerationAdmissionController(2)
    dependencies["get"].side_effect = (
        lambda requested_owner, requested_conversation: _conversation(
            requested_owner,
            requested_conversation,
            2,
        )
    )
    dependencies["context"].side_effect = (
        lambda _owner, requested_conversation, **_kwargs: _context_snapshot(
            (
                _message(
                    requested_conversation,
                    MessageRole.USER,
                    "question",
                    1,
                ),
            )
        )
    )

    async def append(
        _owner,
        requested_conversation,
        role,
        content,
        **_kwargs,
    ):
        return _message(requested_conversation, role, content, 2)

    dependencies["append"].side_effect = append
    both_entered = asyncio.Event()
    release_runtime = asyncio.Event()
    active_runtime_calls = 0
    maximum_runtime_calls = 0

    async def generate(*_args, **_kwargs):
        nonlocal active_runtime_calls, maximum_runtime_calls
        active_runtime_calls += 1
        maximum_runtime_calls = max(maximum_runtime_calls, active_runtime_calls)
        if active_runtime_calls == 2:
            both_entered.set()
        try:
            await release_runtime.wait()
            return TextGenerationResult(content="answer")
        finally:
            active_runtime_calls -= 1

    dependencies["router"].generate.side_effect = generate
    services = [
        ConversationGenerationService(
            AsyncMock(spec=AsyncSession),
            dependencies["catalog"],
            dependencies["router"],
            admission,
        )
        for _position in range(3)
    ]
    first = asyncio.create_task(
        services[0].generate_for_owner(users[0], conversations[0], MODEL_ID)
    )
    second = asyncio.create_task(
        services[1].generate_for_owner(users[1], conversations[1], MODEL_ID)
    )
    await both_entered.wait()

    with pytest.raises(GenerationAdmissionRejectedError):
        await services[2].generate_for_owner(
            users[2],
            conversations[2],
            MODEL_ID,
        )

    assert dependencies["router"].generate.await_count == 2
    assert maximum_runtime_calls == 2
    release_runtime.set()
    await asyncio.gather(first, second)

    assert active_runtime_calls == 0
    assert admission._active_users == set()
    assert admission._active_count == 0
    retry = await services[2].generate_for_owner(
        users[2],
        conversations[2],
        MODEL_ID,
    )
    assert retry.role is MessageRole.ASSISTANT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        TextGenerationRuntimeUnavailableError("domain failure"),
        RuntimeError("unexpected failure"),
    ],
)
async def test_runtime_failures_release_admission_and_allow_retry(
    monkeypatch,
    failure,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            2,
        ),
    )
    dependencies["router"].generate.side_effect = failure
    service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )

    with pytest.raises(type(failure), match=str(failure)):
        await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0
    dependencies["router"].generate.side_effect = None
    dependencies["router"].generate.return_value = TextGenerationResult(
        content="answer"
    )

    result = await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)
    assert result.role is MessageRole.ASSISTANT


@pytest.mark.asyncio
async def test_cancellation_after_user_commit_releases_and_preserves_retry(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "committed user",
        2,
    )
    appended_assistant = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        3,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "initial", 1),
            appended_user,
        ),
        appended=None,
    )

    async def append(_owner, _conversation_id, role, _content, **_kwargs):
        if role is MessageRole.USER:
            return appended_user
        return appended_assistant

    dependencies["append"].side_effect = append
    runtime_entered = asyncio.Event()
    blocked_runtime = asyncio.Event()

    async def generate(*_args, **_kwargs):
        runtime_entered.set()
        await blocked_runtime.wait()
        return TextGenerationResult(content="answer")

    dependencies["router"].generate.side_effect = generate
    service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )
    task = asyncio.create_task(
        service.generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="committed user",
        )
    )
    await runtime_entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert dependencies["append"].await_args_list == [
        call(
            owner_id,
            conversation_id,
            MessageRole.USER,
            "committed user",
        )
    ]
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0

    dependencies["get"].return_value = _conversation(
        owner_id,
        conversation_id,
        3,
    )
    dependencies["router"].generate.side_effect = None
    dependencies["router"].generate.return_value = TextGenerationResult(
        content="answer"
    )
    result = await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert result is appended_assistant
    assert dependencies["append"].await_args_list[-1] == call(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        expected_sequence_number=3,
    )


@pytest.mark.asyncio
async def test_stale_rejection_releases_admission_and_allows_retry(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "question", 1),
        ),
        appended=None,
    )
    service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )

    with pytest.raises(ConversationChangedDuringGenerationError):
        await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        2,
    )
    dependencies["append"].return_value = appended

    result = await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert result is appended
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0


class _TrackingDeadline:
    def __init__(self) -> None:
        self.active = False
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self):
        self.active = True
        self.enter_count += 1
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.active = False
        self.exit_count += 1
        return False

    def expired(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_one_deadline_starts_after_ownership_and_covers_all_stages(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    initial = _message(conversation_id, MessageRole.USER, "initial", 1)
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "follow-up",
        2,
    )
    appended_assistant = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        3,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(initial, appended_user),
        appended=None,
    )
    deadline = _TrackingDeadline()
    deadline_calls: list[float] = []

    def timeout_at(deadline_at):
        assert owner_id in dependencies["admission"]._active_users
        deadline_calls.append(deadline_at)
        return deadline
    events: list[str] = []

    async def get_for_owner(*_args):
        assert deadline_calls == []
        events.append("ownership")
        return _conversation(owner_id, conversation_id, 2)

    async def append(_owner, _conversation, role, _content, **_kwargs):
        assert deadline.active
        events.append(f"append:{role.value}")
        if role is MessageRole.USER:
            return appended_user
        return appended_assistant

    async def context(*_args, **_kwargs):
        assert deadline.active
        events.append("context")
        return _context_snapshot((initial, appended_user))

    async def rollback():
        assert deadline.active
        events.append("rollback")

    async def resolve(_model_id):
        assert deadline.active
        events.append("catalog")
        return _resolved()

    async def generate(*_args, **_kwargs):
        assert deadline.active
        events.append("runtime")
        return TextGenerationResult(content="answer")

    dependencies["get"].side_effect = get_for_owner
    dependencies["append"].side_effect = append
    dependencies["context"].side_effect = context
    session.rollback.side_effect = rollback
    dependencies["catalog"].resolve_model.side_effect = resolve
    dependencies["router"].generate.side_effect = generate
    monkeypatch.setattr(generation_module.asyncio, "timeout_at", timeout_at)

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
        73.25,
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        user_message="follow-up",
    )

    assert result is appended_assistant
    assert events == [
        "ownership",
        "append:user",
        "context",
        "rollback",
        "catalog",
        "runtime",
        "append:assistant",
    ]
    assert len(deadline_calls) == 1
    assert deadline.enter_count == 1
    assert deadline.exit_count == 1
    assert not deadline.active


@pytest.mark.asyncio
async def test_rejected_admission_does_not_create_deadline(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(_message(conversation_id, MessageRole.USER, "question", 1),),
        appended=None,
    )
    timeout_at = Mock()
    monkeypatch.setattr(generation_module.asyncio, "timeout_at", timeout_at)
    service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    )

    async with dependencies["admission"].admit(owner_id):
        with pytest.raises(GenerationAdmissionRejectedError):
            await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)

    dependencies["get"].assert_awaited_once_with(owner_id, conversation_id)
    timeout_at.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked_stage",
    [
        "user_commit",
        "context",
        "rollback",
        "catalog",
        "runtime_request",
        "runtime_response",
        "assistant_commit",
    ],
)
async def test_hard_deadline_releases_admission_at_every_blocked_stage(
    monkeypatch,
    blocked_stage,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    initial = _message(conversation_id, MessageRole.USER, "initial", 1)
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "committed user",
        2,
    )
    appended_assistant = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        3,
    )
    snapshot = _context_snapshot((initial, appended_user))
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=snapshot,
        appended=None,
    )
    entered = asyncio.Event()
    blocker = asyncio.Event()
    user_committed = False
    assistant_persisted = False

    async def block():
        entered.set()
        await blocker.wait()

    async def append(_owner, _conversation, role, _content, **_kwargs):
        nonlocal user_committed, assistant_persisted
        if role is MessageRole.USER:
            if blocked_stage == "user_commit":
                await block()
            user_committed = True
            return appended_user
        if blocked_stage == "assistant_commit":
            await block()
        assistant_persisted = True
        return appended_assistant

    async def context(*_args, **_kwargs):
        if blocked_stage == "context":
            await block()
        return snapshot

    async def rollback():
        if blocked_stage == "rollback":
            await block()

    async def resolve(_model_id):
        if blocked_stage == "catalog":
            await block()
        return _resolved()

    async def generate(*_args, **_kwargs):
        if blocked_stage in {"runtime_request", "runtime_response"}:
            await block()
        return TextGenerationResult(content="answer")

    dependencies["append"].side_effect = append
    dependencies["context"].side_effect = context
    session.rollback.side_effect = rollback
    dependencies["catalog"].resolve_model.side_effect = resolve
    dependencies["router"].generate.side_effect = generate
    service = ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
        0.05,
    )

    task = asyncio.create_task(
        service.generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="committed user",
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)

    with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
        await task

    assert str(captured.value) == "local text generation is unavailable"
    assert blocked_stage not in str(captured.value)
    assert not assistant_persisted
    assert user_committed is (blocked_stage != "user_commit")
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0
    async with dependencies["admission"].admit(owner_id):
        pass
    async with dependencies["admission"].admit(uuid4()):
        pass


@pytest.mark.asyncio
async def test_deadline_after_user_commit_preserves_generation_only_retry(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    initial = _message(conversation_id, MessageRole.USER, "initial", 1)
    appended_user = _message(
        conversation_id,
        MessageRole.USER,
        "committed user",
        2,
    )
    appended_assistant = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        3,
    )
    snapshot = _context_snapshot((initial, appended_user))
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=snapshot,
        appended=None,
    )
    runtime_entered = asyncio.Event()
    blocker = asyncio.Event()
    runtime_calls = 0

    async def append(_owner, _conversation, role, _content, **_kwargs):
        if role is MessageRole.USER:
            return appended_user
        return appended_assistant

    async def generate(*_args, **_kwargs):
        nonlocal runtime_calls
        runtime_calls += 1
        if runtime_calls == 1:
            runtime_entered.set()
            await blocker.wait()
        return TextGenerationResult(content="answer")

    dependencies["append"].side_effect = append
    dependencies["router"].generate.side_effect = generate
    service = ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
        0.05,
    )
    first = asyncio.create_task(
        service.generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="committed user",
        )
    )
    await asyncio.wait_for(runtime_entered.wait(), timeout=1)

    with pytest.raises(TextGenerationRuntimeUnavailableError):
        await first

    assert dependencies["append"].await_args_list == [
        call(
            owner_id,
            conversation_id,
            MessageRole.USER,
            "committed user",
        )
    ]
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0

    service.max_duration_seconds = 1.0
    dependencies["get"].return_value = _conversation(
        owner_id,
        conversation_id,
        3,
    )
    result = await service.generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
    )

    assert result is appended_assistant
    assert dependencies["append"].await_args_list[-1] == call(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        expected_sequence_number=3,
    )


@pytest.mark.asyncio
async def test_non_deadline_timeout_is_not_reclassified(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "question", 1),
        ),
        appended=None,
    )
    dependencies["router"].generate.side_effect = TimeoutError(
        "narrower operation timeout"
    )
    service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
        1.0,
    )

    with pytest.raises(TimeoutError, match="narrower operation timeout"):
        await service.generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0


def _vision_metadata(media_type="image/png", *, position=1):
    return MessageAttachmentMetadata(
        asset_id=uuid4(),
        position=position,
        media_type=media_type,
        byte_size=3,
        content_sha256="a" * 64,
        storage_key=f"objects/{uuid4().hex}",
    )


def _mock_vision_service(monkeypatch, *, metadata):
    vision = Mock(
        resolve_for_owner_message=AsyncMock(return_value=metadata),
        placeholder_images=Mock(
            return_value=tuple("AAAA" for _item in metadata)
        ),
        encode_images=AsyncMock(
            return_value=tuple("cG5n" for _item in metadata)
        ),
    )
    factory = Mock(return_value=vision)
    monkeypatch.setattr(generation_module, "VisionInputService", factory)
    return vision, factory


@pytest.mark.asyncio
async def test_new_owned_images_are_preflighted_read_and_sent_only_on_new_user(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    prior = _message(conversation_id, MessageRole.USER, "prior", 1)
    appended_user = _message(conversation_id, MessageRole.USER, "inspect", 2)
    appended_assistant = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        3,
    )
    first = _vision_metadata(position=1)
    second = _vision_metadata("image/jpeg", position=2)
    attachment_ids = (first.asset_id, second.asset_id)
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(prior, appended_user),
        appended=None,
    )
    dependencies["catalog"].resolve_model.return_value = _resolved(
        capabilities=(
            ModelCapability.TEXT_GENERATION,
            ModelCapability.VISION_INPUT,
        )
    )
    dependencies["router"].request_byte_limit = Mock(return_value=4096)
    events: list[str] = []
    dependencies["router"].preflight = Mock(
        side_effect=lambda *_args, **_kwargs: events.append("preflight")
    )

    async def append(_owner, _conversation, role, _content, **_kwargs):
        return appended_user if role is MessageRole.USER else appended_assistant

    dependencies["append"].side_effect = append
    vision, vision_factory = _mock_vision_service(
        monkeypatch,
        metadata=(first, second),
    )
    vision.encode_images.side_effect = lambda *_args: (
        events.append("read") or ("cG5n", "cG5n")
    )
    dependencies["router"].generate.side_effect = lambda *_args, **_kwargs: (
        events.append("runtime")
        or TextGenerationResult(content="  exact answer  ")
    )
    storage = object()

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
        storage=storage,
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        user_message="inspect",
        attachment_ids=attachment_ids,
    )

    assert result is appended_assistant
    vision_factory.assert_called_once_with(session, storage)
    vision.resolve_for_owner_message.assert_awaited_once_with(
        owner_id,
        conversation_id,
        appended_user.id,
        attachment_ids,
    )
    dependencies["append"].assert_has_awaits(
        [
            call(
                owner_id,
                conversation_id,
                MessageRole.USER,
                "inspect",
                attachment_ids=attachment_ids,
            ),
            call(
                owner_id,
                conversation_id,
                MessageRole.ASSISTANT,
                "  exact answer  ",
                expected_sequence_number=3,
            ),
        ]
    )
    dependencies["catalog"].resolve_model.assert_awaited_once_with(MODEL_ID)
    dependencies["router"].request_byte_limit.assert_called_once_with(
        _resolved(
            capabilities=(
                ModelCapability.TEXT_GENERATION,
                ModelCapability.VISION_INPUT,
            )
        )
    )
    placeholder_context = dependencies["router"].preflight.call_args.args[1]
    assert [message.images for message in placeholder_context] == [(), ("AAAA", "AAAA")]
    generated_context = dependencies["router"].generate.await_args.args[1]
    assert [message.images for message in generated_context] == [(), ("cG5n", "cG5n")]
    vision.placeholder_images.assert_called_once_with((first, second), 4096)
    vision.encode_images.assert_awaited_once_with((first, second))
    assert events == ["preflight", "read", "runtime"]
    session.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_model_without_vision_rejects_before_preflight_or_file_read(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    appended_user = _message(conversation_id, MessageRole.USER, "inspect", 2)
    item = _vision_metadata()
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "prior", 1),
            appended_user,
        ),
        appended=None,
    )
    dependencies["append"].return_value = appended_user
    dependencies["router"].request_byte_limit = Mock()
    dependencies["router"].preflight = Mock()
    vision, _factory = _mock_vision_service(monkeypatch, metadata=(item,))

    with pytest.raises(ConversationGenerationVisionCapabilityError):
        await ConversationGenerationService(
            AsyncMock(spec=AsyncSession),
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
            storage=object(),
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="inspect",
            attachment_ids=(item.asset_id,),
        )

    dependencies["router"].request_byte_limit.assert_not_called()
    dependencies["router"].preflight.assert_not_called()
    vision.placeholder_images.assert_not_called()
    vision.encode_images.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_opaque_attachment_keeps_existing_text_only_generation(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    appended_user = _message(conversation_id, MessageRole.USER, "read", 2)
    appended_assistant = _message(
        conversation_id, MessageRole.ASSISTANT, "answer", 3
    )
    opaque = _vision_metadata("application/pdf")
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "prior", 1),
            appended_user,
        ),
        appended=None,
    )

    async def append(_owner, _conversation, role, _content, **_kwargs):
        return appended_user if role is MessageRole.USER else appended_assistant

    dependencies["append"].side_effect = append
    vision, _factory = _mock_vision_service(monkeypatch, metadata=())

    result = await ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
    ).generate_for_owner(
        owner_id,
        conversation_id,
        MODEL_ID,
        user_message="read",
        attachment_ids=(opaque.asset_id,),
    )

    assert result is appended_assistant
    vision.encode_images.assert_not_awaited()
    generated_context = dependencies["router"].generate.await_args.args[1]
    assert all(not message.images for message in generated_context)


@pytest.mark.asyncio
async def test_vision_preflight_overflow_stops_before_file_read_and_runtime(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    appended_user = _message(conversation_id, MessageRole.USER, "inspect", 2)
    item = _vision_metadata()
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "prior", 1),
            appended_user,
        ),
        appended=appended_user,
    )
    dependencies["catalog"].resolve_model.return_value = _resolved(
        capabilities=(
            ModelCapability.TEXT_GENERATION,
            ModelCapability.VISION_INPUT,
        )
    )
    dependencies["router"].request_byte_limit = Mock(return_value=512)
    dependencies["router"].preflight = Mock(
        side_effect=TextGenerationRequestTooLargeError("private size")
    )
    vision, _factory = _mock_vision_service(monkeypatch, metadata=(item,))

    with pytest.raises(VisionInputTooLargeError) as captured:
        await ConversationGenerationService(
            AsyncMock(spec=AsyncSession),
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
            storage=object(),
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="inspect",
            attachment_ids=(item.asset_id,),
        )

    assert str(captured.value) == "vision input is too large"
    vision.encode_images.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_resolution_failure_rolls_back_read_transaction(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    appended_user = _message(conversation_id, MessageRole.USER, "inspect", 2)
    item = _vision_metadata("image/svg+xml")
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(),
        appended=appended_user,
    )
    vision, _factory = _mock_vision_service(monkeypatch, metadata=())
    vision.resolve_for_owner_message.side_effect = VisionInputUnsupportedError(
        "private metadata"
    )
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(VisionInputUnsupportedError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="inspect",
            attachment_ids=(item.asset_id,),
        )

    session.rollback.assert_awaited_once_with()
    dependencies["context"].assert_not_awaited()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_read_failure_persists_no_assistant_and_releases_admission(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    appended_user = _message(conversation_id, MessageRole.USER, "inspect", 2)
    item = _vision_metadata()
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "prior", 1),
            appended_user,
        ),
        appended=appended_user,
    )
    dependencies["catalog"].resolve_model.return_value = _resolved(
        capabilities=(
            ModelCapability.TEXT_GENERATION,
            ModelCapability.VISION_INPUT,
        )
    )
    dependencies["router"].request_byte_limit = Mock(return_value=512)
    dependencies["router"].preflight = Mock()
    vision, _factory = _mock_vision_service(monkeypatch, metadata=(item,))
    vision.encode_images.side_effect = VisionInputContentUnavailableError(
        "private read"
    )

    with pytest.raises(VisionInputContentUnavailableError):
        await ConversationGenerationService(
            AsyncMock(spec=AsyncSession),
            dependencies["catalog"],
            dependencies["router"],
            dependencies["admission"],
            storage=object(),
        ).generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="inspect",
            attachment_ids=(item.asset_id,),
        )

    dependencies["router"].generate.assert_not_awaited()
    assert dependencies["append"].await_count == 1
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0


@pytest.mark.asyncio
async def test_generation_only_retry_never_resolves_or_replays_images(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 3),
        context=(
            _message(conversation_id, MessageRole.USER, "prior", 1),
            _message(conversation_id, MessageRole.USER, "image prompt", 2),
        ),
        appended=_message(
            conversation_id,
            MessageRole.ASSISTANT,
            "answer",
            3,
        ),
    )
    vision_factory = Mock(
        side_effect=AssertionError("historical images must not be resolved")
    )
    monkeypatch.setattr(generation_module, "VisionInputService", vision_factory)

    await ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
        storage=object(),
    ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    vision_factory.assert_not_called()
    generated_context = dependencies["router"].generate.await_args.args[1]
    assert all(not message.images for message in generated_context)


@pytest.mark.asyncio
async def test_cancellation_during_vision_read_releases_admission(monkeypatch):
    owner_id = uuid4()
    conversation_id = uuid4()
    appended_user = _message(conversation_id, MessageRole.USER, "inspect", 2)
    item = _vision_metadata()
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(
            _message(conversation_id, MessageRole.USER, "prior", 1),
            appended_user,
        ),
        appended=appended_user,
    )
    dependencies["catalog"].resolve_model.return_value = _resolved(
        capabilities=(
            ModelCapability.TEXT_GENERATION,
            ModelCapability.VISION_INPUT,
        )
    )
    dependencies["router"].request_byte_limit = Mock(return_value=512)
    dependencies["router"].preflight = Mock()
    vision, _factory = _mock_vision_service(monkeypatch, metadata=(item,))
    entered = asyncio.Event()
    blocker = asyncio.Event()

    async def blocked_read(_metadata):
        entered.set()
        await blocker.wait()

    vision.encode_images.side_effect = blocked_read
    service = ConversationGenerationService(
        AsyncMock(spec=AsyncSession),
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
        storage=object(),
    )
    task = asyncio.create_task(
        service.generate_for_owner(
            owner_id,
            conversation_id,
            MODEL_ID,
            user_message="inspect",
            attachment_ids=(item.asset_id,),
        )
    )
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    dependencies["router"].generate.assert_not_awaited()
    assert dependencies["append"].await_count == 1
    assert dependencies["admission"]._active_users == set()
    assert dependencies["admission"]._active_count == 0


@pytest.mark.asyncio
async def test_generation_injects_owner_rag_and_persists_exact_citations(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    user_message = _message(
        conversation_id,
        MessageRole.USER,
        "What is the project deadline?",
        1,
    )
    appended = _message(
        conversation_id,
        MessageRole.ASSISTANT,
        "Friday",
        2,
    )
    dependencies = _dependencies(
        monkeypatch,
        conversation=_conversation(owner_id, conversation_id, 2),
        context=(user_message,),
        appended=appended,
        generated=TextGenerationResult(content="Friday"),
    )
    chunk_id = uuid4()
    retrieved = generation_module.RetrievedDocumentChunk(
        chunk_id=chunk_id,
        asset_id=uuid4(),
        content="The project deadline is Friday.",
        score=0.8,
        original_filename="plan.txt",
        provenance_kind="text",
        page_number=None,
        row_start=None,
        row_end=None,
        section=None,
    )
    search = AsyncMock(return_value=(retrieved,))
    document_factory = Mock(return_value=Mock(search_for_owner=search))
    monkeypatch.setattr(generation_module, "DocumentService", document_factory)
    session = AsyncMock(spec=AsyncSession)
    document_admission = asyncio.Semaphore(2)

    result = await ConversationGenerationService(
        session,
        dependencies["catalog"],
        dependencies["router"],
        dependencies["admission"],
        document_admission=document_admission,
    ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    assert result is appended
    document_factory.assert_called_once_with(
        session,
        None,
        document_admission,
    )
    search.assert_awaited_once_with(owner_id, "What is the project deadline?")
    context = dependencies["router"].generate.await_args.args[1]
    assert context[0].role.value == "system"
    assert "untrusted reference data" in context[0].content
    assert "[source 1: plan.txt]" in context[0].content
    assert "The project deadline is Friday." in context[0].content
    assert context[-1].role.value == "user"
    dependencies["append"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "Friday",
        expected_sequence_number=2,
        citation_chunk_ids=(chunk_id,),
    )
