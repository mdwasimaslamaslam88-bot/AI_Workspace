from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.conversation_generation as generation_module
from app.ai.catalog import (
    ModelAvailability,
    ModelDescriptor,
    ModelModality,
    ResolvedModel,
)
from app.ai.generation import (
    TextGenerationResult,
    TextGenerationRuntimeUnavailableError,
)
from app.models import Conversation, Message, MessageRole
from app.services.conversation_generation import (
    MAX_GENERATION_CONTEXT_CHARACTERS,
    MAX_GENERATION_CONTEXT_MESSAGES,
    MAX_GENERATION_OUTPUT_TOKENS,
    MAX_GENERATION_MIN_P,
    MAX_GENERATION_REPEAT_PENALTY,
    MAX_GENERATION_REPEAT_LAST_N,
    MAX_GENERATION_SEED,
    MAX_GENERATION_TEMPERATURE,
    MAX_GENERATION_TOP_K,
    MAX_GENERATION_TOP_P,
    MIN_GENERATION_REPEAT_PENALTY,
    ConversationChangedDuringGenerationError,
    ConversationGenerationContextTooLargeError,
    ConversationGenerationModelNotFoundError,
    ConversationGenerationModelUnavailableError,
    ConversationGenerationNotFoundError,
    ConversationGenerationNotReadyError,
    ConversationGenerationService,
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
) -> ResolvedModel:
    return ResolvedModel(
        descriptor=ModelDescriptor(
            model_id=MODEL_ID,
            display_name="Local model",
            runtime_id="local-runtime",
            modality=ModelModality.TEXT,
            family=None,
            parameter_class="70B+",
            capabilities=(),
            context_window=None,
            quantization=None,
            estimated_vram_bytes=None,
            availability=availability,
        ),
        runtime_reference="private-runtime-reference",
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
    context_for_owner = AsyncMock(return_value=context)
    append_for_owner = AsyncMock(return_value=appended)
    message_factory = Mock(
        return_value=Mock(
            list_generation_context_for_owner=context_for_owner,
            append_for_owner=append_for_owner,
        )
    )
    catalog = Mock(resolve_model=AsyncMock(return_value=_resolved()))
    router = Mock(generate=AsyncMock(return_value=generated))
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
        events.append("context") or messages
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
        events.append("context") or messages
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
        context=(
            _message(conversation_id, MessageRole.USER, "existing", 1),
            appended_user,
            _message(
                conversation_id,
                MessageRole.USER,
                "x" * (MAX_GENERATION_CONTEXT_CHARACTERS + 1),
                3,
            ),
        ),
        appended=appended_user,
    )

    with pytest.raises(ConversationChangedDuringGenerationError):
        await ConversationGenerationService(
            session,
            dependencies["catalog"],
            dependencies["router"],
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
        context = tuple(
            _message(
                conversation_id,
                MessageRole.USER,
                "x",
                sequence,
            )
            for sequence in range(1, MAX_GENERATION_CONTEXT_MESSAGES + 2)
        )
        next_sequence = MAX_GENERATION_CONTEXT_MESSAGES + 2
    else:
        context = (
            _message(
                conversation_id,
                MessageRole.USER,
                "x" * (MAX_GENERATION_CONTEXT_CHARACTERS + 1),
                1,
            ),
        )
        next_sequence = 2
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
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    session.rollback.assert_awaited_once_with()
    dependencies["catalog"].resolve_model.assert_not_awaited()
    dependencies["router"].generate.assert_not_awaited()


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
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    dependencies["router"].generate.assert_not_awaited()
    dependencies["append"].assert_not_awaited()


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
        ).generate_for_owner(owner_id, conversation_id, MODEL_ID)

    dependencies["router"].generate.assert_awaited_once()
    dependencies["append"].assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "  exact answer  ",
        expected_sequence_number=2,
    )
