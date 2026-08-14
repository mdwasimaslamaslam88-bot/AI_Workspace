from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import app.clients.ollama as ollama_client_module
from app.ai.catalog import ModelCapability, ModelRuntimeUnavailableError
from app.ai.generation import (
    TextGenerationMessage,
    TextGenerationRole,
    TextGenerationRuntimeUnavailableError,
    TextGenerationRuntimeUnsupportedError,
)
from app.clients.ollama import create_ollama_client
from app.core.config import Settings
from app.runtimes.ollama import (
    OllamaModelDiscoveryRuntime,
    OllamaTextGenerationRuntime,
)

LOCAL_MODEL_REFERENCE = "/private/runtime/model:14b"
LOCAL_MODEL_ALLOWLIST = (LOCAL_MODEL_REFERENCE,)


@pytest.mark.asyncio
async def test_ollama_discovery_uses_only_inventory_and_hides_raw_reference():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "/private/models/secret:14b",
                        "details": {
                            "family": "LocalFamily",
                            "parameter_size": "14B",
                            "quantization_level": "Q4_K_M",
                        },
                        "capabilities": [
                            "CHAT",
                            "text-generation",
                            "chat",
                            "unknown-capability",
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            ("/private/models/secret:14b",),
        ).discover_models()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags")
    ]
    assert model.reference == "/private/models/secret:14b"
    assert model.display_name == "LocalFamily 14B"
    assert model.family == "LocalFamily"
    assert model.parameter_class == "14B"
    assert model.quantization == "Q4_K_M"
    assert model.capabilities == (
        ModelCapability.CHAT,
        ModelCapability.TEXT_GENERATION,
    )


@pytest.mark.asyncio
async def test_ollama_discovery_fails_closed_for_non_allowlisted_models():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": LOCAL_MODEL_REFERENCE,
                        "details": {"family": "VerifiedLocal"},
                    },
                    {
                        "model": "gpt-oss:120b-cloud",
                        "details": {"family": "Cloud"},
                    },
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        empty_inventory = await OllamaModelDiscoveryRuntime(
            client
        ).discover_models()
        allowed_inventory = await OllamaModelDiscoveryRuntime(
            client,
            LOCAL_MODEL_ALLOWLIST,
        ).discover_models()

    assert empty_inventory == ()
    assert tuple(model.reference for model in allowed_inventory) == (
        LOCAL_MODEL_REFERENCE,
    )


@pytest.mark.asyncio
async def test_ollama_unsafe_metadata_is_not_promoted_to_public_fields():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": "internal-tag",
                        "details": {
                            "family": "/private/models/family",
                            "parameter_size": "https://external.invalid/model",
                            "quantization_level": "C:\\private\\model",
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            ("internal-tag",),
        ).discover_models()

    assert model.display_name == "Local text model"
    assert model.family is None
    assert model.parameter_class is None
    assert model.quantization is None


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"models": None}, {"models": [None]}, {"models": [{}]}],
)
@pytest.mark.asyncio
async def test_ollama_malformed_inventory_is_generic_runtime_unavailable(payload):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError):
            await OllamaModelDiscoveryRuntime(client).discover_models()


@pytest.mark.parametrize("failure", ["timeout", "status"])
@pytest.mark.asyncio
async def test_ollama_transport_failures_are_generic_runtime_unavailable(failure):
    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ConnectTimeout("secret runtime URL", request=request)
        return httpx.Response(503, text="secret runtime persistence detail")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(client).discover_models()

    assert "secret" not in str(captured.value)


def test_ollama_client_disables_proxies_and_redirects(monkeypatch):
    client = object()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(ollama_client_module.httpx, "AsyncClient", constructor)
    settings = Settings(
        _env_file=None,
        OLLAMA_BASE_URL="http://127.0.0.1:11434",
        OLLAMA_TIMEOUT_SECONDS=7,
    )

    assert create_ollama_client(settings) is client
    constructor.assert_called_once_with(
        base_url="http://127.0.0.1:11434",
        timeout=7,
        follow_redirects=False,
        trust_env=False,
    )


@pytest.mark.asyncio
async def test_ollama_generation_is_non_streaming_bounded_and_preserves_content():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "  exact generated content  ",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.SYSTEM,
                    content="system",
                ),
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="  exact prompt  ",
                ),
            ),
            max_output_tokens=1024,
        )

    assert result.content == "  exact generated content  "
    assert len(requests) == 1
    request = requests[0]
    assert (request.method, request.url.path) == ("POST", "/api/chat")
    assert request.extensions["timeout"] == {
        "connect": 37.0,
        "read": 37.0,
        "write": 37.0,
        "pool": 37.0,
    }
    assert request.read()
    import json

    assert json.loads(request.content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "  exact prompt  "},
        ],
        "stream": False,
        "options": {"num_predict": 1024},
    }


@pytest.mark.parametrize("temperature", [0, 1, 2, 0.5, 2.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_temperature(temperature):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            temperature=temperature,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "temperature": temperature,
        },
    }


@pytest.mark.parametrize("seed", [0, 42, 2_147_483_647])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_seed(seed):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            seed=seed,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "seed": seed,
        },
    }


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
async def test_ollama_generation_rejects_invalid_temperature_before_http(
    temperature,
):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            temperature=temperature,
        )

    client.post.assert_not_awaited()


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
async def test_ollama_generation_rejects_invalid_seed_before_http(seed):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            seed=seed,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"done": True, "message": None},
        {
            "message": {
                "role": "assistant",
                "content": "apparently complete",
            },
        },
        *[
            {
                "done": done,
                "message": {
                    "role": "assistant",
                    "content": "apparently complete",
                },
            }
            for done in (None, False, 1, "true")
        ],
        {"done": True, "message": {}},
        {
            "done": True,
            "message": {"role": "user", "content": "answer"},
        },
        {"done": True, "message": {"role": "assistant"}},
        {
            "done": True,
            "message": {"role": "assistant", "content": ""},
        },
        {
            "done": True,
            "message": {"role": "assistant", "content": "   "},
        },
    ],
)
@pytest.mark.asyncio
async def test_ollama_malformed_generation_is_generic_unavailable(payload):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await OllamaTextGenerationRuntime(
                client,
                10,
                ("secret-model-tag",),
            ).generate_text(
                "secret-model-tag",
                (
                    TextGenerationMessage(
                        role=TextGenerationRole.USER,
                        content="secret prompt",
                    ),
                ),
                max_output_tokens=1024,
            )

    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_generation_rechecks_local_allowlist_before_http():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {"role": "assistant", "content": "unsafe"},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnsupportedError):
            await OllamaTextGenerationRuntime(
                client,
                10,
                LOCAL_MODEL_ALLOWLIST,
            ).generate_text(
                "gpt-oss:120b-cloud",
                (
                    TextGenerationMessage(
                        role=TextGenerationRole.USER,
                        content="private conversation",
                    ),
                ),
                max_output_tokens=1024,
            )

    assert requests == []


@pytest.mark.parametrize(
    "timeout_seconds",
    [float("inf"), float("-inf"), float("nan")],
)
def test_ollama_generation_timeout_must_be_finite(timeout_seconds):
    with pytest.raises(ValueError, match="positive and finite"):
        OllamaTextGenerationRuntime(
            object(), timeout_seconds, LOCAL_MODEL_ALLOWLIST
        )


@pytest.mark.parametrize("failure", ["timeout", "status"])
@pytest.mark.asyncio
async def test_ollama_generation_transport_failures_are_generic(failure):
    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("secret runtime detail", request=request)
        return httpx.Response(500, text="secret runtime body")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await OllamaTextGenerationRuntime(
                client,
                10,
                ("secret-model-tag",),
            ).generate_text(
                "secret-model-tag",
                (
                    TextGenerationMessage(
                        role=TextGenerationRole.USER,
                        content="prompt",
                    ),
                ),
                max_output_tokens=1024,
            )

    assert "secret" not in str(captured.value)
