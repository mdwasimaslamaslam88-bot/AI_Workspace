from unittest.mock import AsyncMock, Mock

import pytest

from app.ai.catalog import (
    ModelAvailability,
    ModelDescriptor,
    ModelModality,
    ResolvedModel,
)
from app.ai.generation import (
    TextGenerationMessage,
    TextGenerationResult,
    TextGenerationRole,
    TextGenerationRouter,
    TextGenerationRuntimeUnsupportedError,
)


def _resolved_model(
    *,
    runtime_id: str = "local-runtime",
    parameter_class: str | None = "7B",
) -> ResolvedModel:
    return ResolvedModel(
        descriptor=ModelDescriptor(
            model_id=f"{runtime_id}:{'a' * 24}",
            display_name="Local model",
            runtime_id=runtime_id,
            modality=ModelModality.TEXT,
            family=None,
            parameter_class=parameter_class,
            capabilities=(),
            context_window=None,
            quantization=None,
            estimated_vram_bytes=None,
            availability=ModelAvailability.AVAILABLE,
        ),
        runtime_reference="/private/runtime/model:tag",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter_class", ["7B", "14B", "32B", "70B+"])
async def test_router_dispatches_by_runtime_not_parameter_class(parameter_class):
    generated = TextGenerationResult(content="  exact local answer  ")
    generate_text = AsyncMock(return_value=generated)
    runtime = Mock(runtime_id="local-runtime", generate_text=generate_text)
    router = TextGenerationRouter((runtime,))
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="  exact prompt  ",
        ),
    )

    result = await router.generate(
        _resolved_model(parameter_class=parameter_class),
        messages,
        max_output_tokens=1024,
    )

    assert result is generated
    generate_text.assert_awaited_once_with(
        "/private/runtime/model:tag",
        messages,
        max_output_tokens=1024,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
    )


@pytest.mark.asyncio
async def test_router_rejects_model_without_registered_generation_adapter():
    with pytest.raises(TextGenerationRuntimeUnsupportedError):
        await TextGenerationRouter().generate(
            _resolved_model(runtime_id="discovery-only"),
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=1024,
        )


def test_router_rejects_duplicate_runtime_ids():
    first = Mock(runtime_id="duplicate")
    second = Mock(runtime_id="duplicate")

    with pytest.raises(ValueError, match="duplicate text-generation runtime_id"):
        TextGenerationRouter((first, second))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"role": "user", "content": "prompt"},
        {"role": TextGenerationRole.USER, "content": 3},
    ],
)
def test_generation_message_validates_runtime_neutral_values(kwargs):
    with pytest.raises(TypeError):
        TextGenerationMessage(**kwargs)


@pytest.mark.parametrize("content", ["", "   ", "\n\t"])
def test_generation_result_rejects_blank_content(content):
    with pytest.raises(ValueError, match="must not be blank"):
        TextGenerationResult(content=content)


@pytest.mark.parametrize(
    "max_output_tokens",
    [True, 0, -1, 1.5, "1024"],
)
@pytest.mark.asyncio
async def test_router_rejects_invalid_output_bounds(max_output_tokens):
    runtime = Mock(
        runtime_id="local-runtime",
        generate_text=AsyncMock(),
    )

    with pytest.raises((TypeError, ValueError)):
        await TextGenerationRouter((runtime,)).generate(
            _resolved_model(),
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=max_output_tokens,
        )

    runtime.generate_text.assert_not_awaited()


@pytest.mark.parametrize("temperature", [0, 1, 2, 0.5, 2.0])
@pytest.mark.asyncio
async def test_router_forwards_exact_valid_temperature(temperature):
    generated = TextGenerationResult(content="answer")
    generate_text = AsyncMock(return_value=generated)
    runtime = Mock(runtime_id="local-runtime", generate_text=generate_text)
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="prompt",
        ),
    )

    result = await TextGenerationRouter((runtime,)).generate(
        _resolved_model(),
        messages,
        max_output_tokens=1024,
        temperature=temperature,
    )

    assert result is generated
    generate_text.assert_awaited_once_with(
        "/private/runtime/model:tag",
        messages,
        max_output_tokens=1024,
        temperature=temperature,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
    )


@pytest.mark.parametrize("seed", [0, 42, 2_147_483_647])
@pytest.mark.asyncio
async def test_router_forwards_exact_valid_seed(seed):
    generated = TextGenerationResult(content="answer")
    generate_text = AsyncMock(return_value=generated)
    runtime = Mock(runtime_id="local-runtime", generate_text=generate_text)
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="prompt",
        ),
    )

    result = await TextGenerationRouter((runtime,)).generate(
        _resolved_model(),
        messages,
        max_output_tokens=1024,
        seed=seed,
    )

    assert result is generated
    generate_text.assert_awaited_once_with(
        "/private/runtime/model:tag",
        messages,
        max_output_tokens=1024,
        temperature=None,
        seed=seed,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
    )


@pytest.mark.parametrize("top_p", [0, 1, 0.5, 0.9, 1.0])
@pytest.mark.asyncio
async def test_router_forwards_exact_valid_top_p(top_p):
    generated = TextGenerationResult(content="answer")
    generate_text = AsyncMock(return_value=generated)
    runtime = Mock(runtime_id="local-runtime", generate_text=generate_text)
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="prompt",
        ),
    )

    result = await TextGenerationRouter((runtime,)).generate(
        _resolved_model(),
        messages,
        max_output_tokens=1024,
        top_p=top_p,
    )

    assert result is generated
    generate_text.assert_awaited_once_with(
        "/private/runtime/model:tag",
        messages,
        max_output_tokens=1024,
        temperature=None,
        seed=None,
        top_p=top_p,
        top_k=None,
        min_p=None,
        repeat_penalty=None,
    )


@pytest.mark.parametrize("top_k", [1, 40, 100])
@pytest.mark.asyncio
async def test_router_forwards_exact_valid_top_k(top_k):
    generated = TextGenerationResult(content="answer")
    generate_text = AsyncMock(return_value=generated)
    runtime = Mock(runtime_id="local-runtime", generate_text=generate_text)
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="prompt",
        ),
    )

    result = await TextGenerationRouter((runtime,)).generate(
        _resolved_model(),
        messages,
        max_output_tokens=1024,
        top_k=top_k,
    )

    assert result is generated
    generate_text.assert_awaited_once_with(
        "/private/runtime/model:tag",
        messages,
        max_output_tokens=1024,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=top_k,
        min_p=None,
        repeat_penalty=None,
    )


@pytest.mark.parametrize("min_p", [0, 1, 0.05, 0.5, 1.0])
@pytest.mark.asyncio
async def test_router_forwards_exact_valid_min_p(min_p):
    generated = TextGenerationResult(content="answer")
    generate_text = AsyncMock(return_value=generated)
    runtime = Mock(runtime_id="local-runtime", generate_text=generate_text)
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="prompt",
        ),
    )

    result = await TextGenerationRouter((runtime,)).generate(
        _resolved_model(),
        messages,
        max_output_tokens=1024,
        min_p=min_p,
    )

    assert result is generated
    generate_text.assert_awaited_once_with(
        "/private/runtime/model:tag",
        messages,
        max_output_tokens=1024,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=min_p,
        repeat_penalty=None,
    )


@pytest.mark.parametrize("repeat_penalty", [0.5, 0.9, 1, 1.1, 1.5, 2.0])
@pytest.mark.asyncio
async def test_router_forwards_exact_valid_repeat_penalty(repeat_penalty):
    generated = TextGenerationResult(content="answer")
    generate_text = AsyncMock(return_value=generated)
    runtime = Mock(runtime_id="local-runtime", generate_text=generate_text)
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="prompt",
        ),
    )

    result = await TextGenerationRouter((runtime,)).generate(
        _resolved_model(),
        messages,
        max_output_tokens=1024,
        repeat_penalty=repeat_penalty,
    )

    assert result is generated
    generate_text.assert_awaited_once_with(
        "/private/runtime/model:tag",
        messages,
        max_output_tokens=1024,
        temperature=None,
        seed=None,
        top_p=None,
        top_k=None,
        min_p=None,
        repeat_penalty=repeat_penalty,
    )


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
        2.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_router_rejects_invalid_temperature_before_runtime(
    temperature,
):
    runtime = Mock(
        runtime_id="local-runtime",
        generate_text=AsyncMock(),
    )

    with pytest.raises((TypeError, ValueError)):
        await TextGenerationRouter((runtime,)).generate(
            _resolved_model(),
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=1024,
            temperature=temperature,
        )

    runtime.generate_text.assert_not_awaited()


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
        1.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_router_rejects_invalid_min_p_before_runtime(min_p):
    runtime = Mock(
        runtime_id="local-runtime",
        generate_text=AsyncMock(),
    )

    with pytest.raises((TypeError, ValueError)):
        await TextGenerationRouter((runtime,)).generate(
            _resolved_model(),
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=1024,
            min_p=min_p,
        )

    runtime.generate_text.assert_not_awaited()


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
        0.49,
        2.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_router_rejects_invalid_repeat_penalty_before_runtime(
    repeat_penalty,
):
    runtime = Mock(
        runtime_id="local-runtime",
        generate_text=AsyncMock(),
    )

    with pytest.raises((TypeError, ValueError)):
        await TextGenerationRouter((runtime,)).generate(
            _resolved_model(),
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=1024,
            repeat_penalty=repeat_penalty,
        )

    runtime.generate_text.assert_not_awaited()


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
        2_147_483_648,
    ],
)
@pytest.mark.asyncio
async def test_router_rejects_invalid_seed_before_runtime(seed):
    runtime = Mock(
        runtime_id="local-runtime",
        generate_text=AsyncMock(),
    )

    with pytest.raises((TypeError, ValueError)):
        await TextGenerationRouter((runtime,)).generate(
            _resolved_model(),
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=1024,
            seed=seed,
        )

    runtime.generate_text.assert_not_awaited()


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
        1.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_router_rejects_invalid_top_p_before_runtime(top_p):
    runtime = Mock(
        runtime_id="local-runtime",
        generate_text=AsyncMock(),
    )

    with pytest.raises((TypeError, ValueError)):
        await TextGenerationRouter((runtime,)).generate(
            _resolved_model(),
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=1024,
            top_p=top_p,
        )

    runtime.generate_text.assert_not_awaited()


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
        101,
    ],
)
@pytest.mark.asyncio
async def test_router_rejects_invalid_top_k_before_runtime(top_k):
    runtime = Mock(
        runtime_id="local-runtime",
        generate_text=AsyncMock(),
    )

    with pytest.raises((TypeError, ValueError)):
        await TextGenerationRouter((runtime,)).generate(
            _resolved_model(),
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=1024,
            top_k=top_k,
        )

    runtime.generate_text.assert_not_awaited()
