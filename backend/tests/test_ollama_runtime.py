import asyncio
import json
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
from app.core.config import (
    MAX_OLLAMA_GENERATION_RESPONSE_BYTES,
    Settings,
)
from app.runtimes.ollama import (
    OllamaModelDiscoveryRuntime,
    OllamaTextGenerationRuntime,
)

LOCAL_MODEL_REFERENCE = "/private/runtime/model:14b"
LOCAL_MODEL_ALLOWLIST = (LOCAL_MODEL_REFERENCE,)


@pytest.mark.asyncio
async def test_ollama_discovery_uses_documented_capabilities_and_hides_reference():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": [
                        "vision",
                        "completion",
                        "embedding",
                        "tools",
                        "completion",
                        "thinking",
                        "unknown-capability",
                    ],
                    "template": "must not infer chat",
                    "parameters": "must not infer settings",
                    "license": "must not become public metadata",
                    "model_info": {"private.path": "/private/model"},
                },
            )
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
        ("GET", "/api/tags"),
        ("POST", "/api/show"),
    ]
    assert json.loads(requests[1].content) == {
        "model": "/private/models/secret:14b"
    }
    assert model.reference == "/private/models/secret:14b"
    assert model.display_name == "LocalFamily 14B"
    assert model.family == "LocalFamily"
    assert model.parameter_class == "14B"
    assert model.quantization == "Q4_K_M"
    assert model.capabilities == (
        ModelCapability.EMBEDDINGS,
        ModelCapability.TEXT_GENERATION,
        ModelCapability.TOOL_CALLING,
        ModelCapability.VISION_INPUT,
    )


@pytest.mark.asyncio
async def test_ollama_discovery_fails_closed_for_non_allowlisted_models():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
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
        absent_inventory = await OllamaModelDiscoveryRuntime(
            client,
            ("absent-local-model",),
        ).discover_models()
        allowed_inventory = await OllamaModelDiscoveryRuntime(
            client,
            LOCAL_MODEL_ALLOWLIST,
        ).discover_models()

    assert empty_inventory == ()
    assert absent_inventory == ()
    assert tuple(model.reference for model in allowed_inventory) == (
        LOCAL_MODEL_REFERENCE,
    )
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags"),
        ("GET", "/api/tags"),
        ("GET", "/api/tags"),
        ("POST", "/api/show"),
    ]
    assert json.loads(requests[3].content) == {
        "model": LOCAL_MODEL_REFERENCE
    }


@pytest.mark.asyncio
async def test_ollama_unsafe_metadata_is_not_promoted_to_public_fields():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion"],
                    "template": "/private/template",
                    "parameters": "https://external.invalid/settings",
                    "license": "C:\\private\\license",
                    "model_info": {"secret": "/private/model"},
                },
            )
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
    assert model.capabilities == (ModelCapability.TEXT_GENERATION,)


@pytest.mark.parametrize(
    "detail_payload",
    [
        None,
        [],
        {},
        {"capabilities": None},
        {"capabilities": "completion"},
        {"capabilities": ["completion", 1]},
    ],
)
@pytest.mark.asyncio
async def test_ollama_discovery_rejects_malformed_capability_details(
    detail_payload,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json=detail_payload)
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": LOCAL_MODEL_REFERENCE,
                        "details": {"family": "VerifiedLocal"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError):
            await OllamaModelDiscoveryRuntime(
                client,
                LOCAL_MODEL_ALLOWLIST,
            ).discover_models()


@pytest.mark.asyncio
async def test_ollama_discovery_rejects_unavailable_capability_details():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(503, text="secret runtime detail")
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": LOCAL_MODEL_REFERENCE,
                        "details": {"family": "VerifiedLocal"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(
            ModelRuntimeUnavailableError,
            match="inventory is unavailable",
        ) as raised:
            await OllamaModelDiscoveryRuntime(
                client,
                LOCAL_MODEL_ALLOWLIST,
            ).discover_models()

    assert "secret runtime detail" not in str(raised.value)


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
        2049,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_repeat_last_n_before_http(
    repeat_last_n,
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
            repeat_last_n=repeat_last_n,
        )

    client.post.assert_not_awaited()


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
        1.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_typical_p_before_http(
    typical_p,
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
            typical_p=typical_p,
        )

    client.post.assert_not_awaited()


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
        -2.01,
        2.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_presence_penalty_before_http(
    presence_penalty,
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
            presence_penalty=presence_penalty,
        )

    client.post.assert_not_awaited()


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
        -2.01,
        2.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_frequency_penalty_before_http(
    frequency_penalty,
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
            frequency_penalty=frequency_penalty,
        )

    client.post.assert_not_awaited()


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
        ["x" * 129],
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_stop_sequences_before_http(
    stop_sequences,
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
            stop_sequences=stop_sequences,
        )

    client.post.assert_not_awaited()


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
    assert request.headers["Accept-Encoding"] == "identity"
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


@pytest.mark.parametrize("top_p", [0, 1, 0.5, 0.9, 1.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_top_p(top_p):
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
            top_p=top_p,
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
            "top_p": top_p,
        },
    }


@pytest.mark.parametrize("top_k", [1, 40, 100])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_top_k(top_k):
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
            top_k=top_k,
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
            "top_k": top_k,
        },
    }


@pytest.mark.parametrize("min_p", [0, 1, 0.05, 0.5, 1.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_min_p(min_p):
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
            min_p=min_p,
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
            "min_p": min_p,
        },
    }


@pytest.mark.parametrize("repeat_penalty", [0.5, 0.9, 1, 1.1, 1.5, 2.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_repeat_penalty(
    repeat_penalty,
):
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
            repeat_penalty=repeat_penalty,
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
            "repeat_penalty": repeat_penalty,
        },
    }


@pytest.mark.parametrize("repeat_last_n", [0, 1, 64, 2048])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_repeat_last_n(
    repeat_last_n,
):
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
            repeat_last_n=repeat_last_n,
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
            "repeat_last_n": repeat_last_n,
        },
    }


@pytest.mark.parametrize("typical_p", [0, 1, 0.05, 0.7, 1.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_typical_p(typical_p):
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
            typical_p=typical_p,
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
            "typical_p": typical_p,
        },
    }


@pytest.mark.parametrize(
    "presence_penalty",
    [-2, -1, 0, 0.5, 1, 1.5, 2.0],
)
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_presence_penalty(
    presence_penalty,
):
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
            presence_penalty=presence_penalty,
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
            "presence_penalty": presence_penalty,
        },
    }


@pytest.mark.parametrize(
    "frequency_penalty",
    [-2, -1, 0, 0.5, 1, 1.5, 2.0],
)
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_frequency_penalty(
    frequency_penalty,
):
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
            frequency_penalty=frequency_penalty,
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
            "frequency_penalty": frequency_penalty,
        },
    }


@pytest.mark.parametrize(
    "stop_sequences",
    [
        ["END"],
        ["\n", "\t", "\n", "\u0000"],
        ["界" * 128],
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_stop_sequences(
    stop_sequences,
):
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
            stop_sequences=stop_sequences,
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
            "stop": stop_sequences,
        },
    }


@pytest.mark.asyncio
async def test_ollama_generation_combines_all_bounded_options():
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
            temperature=0.5,
            seed=42,
            top_p=0.9,
            top_k=40,
            min_p=0.05,
            repeat_penalty=1.1,
            repeat_last_n=64,
            typical_p=0.7,
            presence_penalty=1.5,
            frequency_penalty=0.75,
            stop_sequences=["\n", "END", "\n"],
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
            "temperature": 0.5,
            "seed": 42,
            "top_p": 0.9,
            "top_k": 40,
            "min_p": 0.05,
            "repeat_penalty": 1.1,
            "repeat_last_n": 64,
            "typical_p": 0.7,
            "presence_penalty": 1.5,
            "frequency_penalty": 0.75,
            "stop": ["\n", "END", "\n"],
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
async def test_ollama_generation_rejects_invalid_top_p_before_http(top_p):
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
            top_p=top_p,
        )

    client.post.assert_not_awaited()


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
async def test_ollama_generation_rejects_invalid_top_k_before_http(top_k):
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
            top_k=top_k,
        )

    client.post.assert_not_awaited()


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
async def test_ollama_generation_rejects_invalid_min_p_before_http(min_p):
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
            min_p=min_p,
        )

    client.post.assert_not_awaited()


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
async def test_ollama_generation_rejects_invalid_repeat_penalty_before_http(
    repeat_penalty,
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
            repeat_penalty=repeat_penalty,
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


class _RecordingAsyncByteStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks,
        *,
        blocked: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ):
        self.chunks = tuple(chunks)
        self.blocked = blocked
        self.release = release
        self.iteration_count = 0
        self.closed = False

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            self.iteration_count += 1
            yield chunk
            if index == 0 and self.blocked is not None:
                self.blocked.set()
                if self.release is None:
                    raise AssertionError("blocked stream requires a release event")
                await self.release.wait()

    async def aclose(self):
        self.closed = True


def _generation_response_body(content: str) -> bytes:
    return json.dumps(
        {
            "done": True,
            "message": {
                "role": "assistant",
                "content": content,
            },
        },
        separators=(",", ":"),
    ).encode()


def _exact_generation_response_body(size: int) -> bytes:
    empty = _generation_response_body("")
    content_size = size - len(empty)
    if content_size < 1:
        raise ValueError("test response size cannot hold nonblank content")
    body = _generation_response_body("x" * content_size)
    assert len(body) == size
    return body


def _generation_transport(
    stream: _RecordingAsyncByteStream,
    *,
    status_code: int = 200,
    headers=(),
):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code,
            headers=list(headers),
            stream=stream,
        )

    return httpx.MockTransport(handler), requests


async def _generate_with_runtime(
    runtime: OllamaTextGenerationRuntime,
    *,
    max_output_tokens: int = 128,
):
    return await runtime.generate_text(
        LOCAL_MODEL_REFERENCE,
        (
            TextGenerationMessage(
                role=TextGenerationRole.USER,
                content="prompt",
            ),
        ),
        max_output_tokens=max_output_tokens,
    )


@pytest.mark.parametrize(
    "max_response_bytes",
    [
        None,
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_OLLAMA_GENERATION_RESPONSE_BYTES + 1,
    ],
)
def test_ollama_generation_runtime_rejects_invalid_response_caps(
    max_response_bytes,
):
    with pytest.raises((TypeError, ValueError)):
        OllamaTextGenerationRuntime(
            object(),
            10,
            LOCAL_MODEL_ALLOWLIST,
            max_response_bytes=max_response_bytes,
        )


@pytest.mark.parametrize(
    "max_response_bytes",
    [1, 262_144, MAX_OLLAMA_GENERATION_RESPONSE_BYTES],
)
def test_ollama_generation_runtime_accepts_bounded_response_caps(
    max_response_bytes,
):
    runtime = OllamaTextGenerationRuntime(
        object(),
        10,
        LOCAL_MODEL_ALLOWLIST,
        max_response_bytes=max_response_bytes,
    )

    assert runtime.max_response_bytes == max_response_bytes


@pytest.mark.asyncio
async def test_ollama_generation_accepts_valid_response_exactly_at_cap():
    cap = 128
    body = _exact_generation_response_body(cap)
    stream = _RecordingAsyncByteStream((body[:47], body[47:]))
    transport, requests = _generation_transport(
        stream,
        headers=((b"content-length", str(cap).encode()),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        result = await _generate_with_runtime(
            OllamaTextGenerationRuntime(
                client,
                10,
                LOCAL_MODEL_ALLOWLIST,
                max_response_bytes=cap,
            )
        )

    assert result.content == "x" * (
        cap - len(_generation_response_body(""))
    )
    assert len(requests) == 1
    assert stream.iteration_count == 2
    assert stream.closed is True


@pytest.mark.asyncio
async def test_ollama_generation_absent_length_stops_at_cap_plus_one_and_is_safe(
    caplog,
):
    cap = 65_537
    response_fragment = b"private-response-fragment credential-never-consumed"
    stream = _RecordingAsyncByteStream(
        (
            b"x" * cap,
            b"y",
            response_fragment,
        )
    )
    transport, requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=cap,
                ),
                max_output_tokens=1,
            )

    assert stream.iteration_count == 2
    assert stream.closed is True
    assert len(requests) == 1
    outgoing = json.loads(requests[0].content)
    assert outgoing["stream"] is False
    assert outgoing["options"]["num_predict"] == 1
    safe_output = str(captured.value) + caplog.text
    for unsafe in (
        response_fragment.decode(),
        "credential-never-consumed",
        str(cap),
        str(cap + 1),
        LOCAL_MODEL_REFERENCE,
    ):
        assert unsafe not in safe_output


@pytest.mark.asyncio
async def test_ollama_generation_understated_length_cannot_bypass_actual_cap():
    cap = 64
    stream = _RecordingAsyncByteStream((b"x" * cap, b"y"))
    transport, _requests = _generation_transport(
        stream,
        headers=((b"content-length", b"1"),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError):
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=cap,
                )
            )

    assert stream.iteration_count == 2
    assert stream.closed is True


@pytest.mark.asyncio
async def test_ollama_generation_declared_oversize_does_not_iterate_body():
    cap = 64
    stream = _RecordingAsyncByteStream((b"must-not-be-consumed",))
    transport, _requests = _generation_transport(
        stream,
        headers=((b"content-length", str(cap + 1).encode()),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError):
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=cap,
                )
            )

    assert stream.iteration_count == 0
    assert stream.closed is True


@pytest.mark.parametrize(
    ("headers", "private_header"),
    [
        (
            ((b"content-length", b"invalid-private-length"),),
            "invalid-private-length",
        ),
        (((b"content-length", b"-123456"),), "-123456"),
        (((b"content-length", b"123456, 123456"),), "123456, 123456"),
        (
            (
                (b"content-length", b"123456"),
                (b"content-length", b"234567"),
            ),
            "123456",
        ),
    ],
    ids=["malformed", "negative", "comma-ambiguous", "conflicting-duplicate"],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_unsafe_length_headers_without_reading(
    headers,
    private_header,
    caplog,
):
    stream = _RecordingAsyncByteStream((b"must-not-be-consumed",))
    transport, _requests = _generation_transport(stream, headers=headers)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=64,
                )
            )

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert private_header not in (str(captured.value) + caplog.text)


@pytest.mark.asyncio
async def test_ollama_generation_non_success_status_does_not_consume_body():
    stream = _RecordingAsyncByteStream((b"large private runtime error",))
    transport, _requests = _generation_transport(
        stream,
        status_code=503,
        headers=((b"content-length", b"999999"),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=64,
                )
            )

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert "large private runtime error" not in str(captured.value)
    assert "503" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_generation_rejects_compressed_response_before_reading():
    stream = _RecordingAsyncByteStream((b"compressed-private-body",))
    transport, _requests = _generation_transport(
        stream,
        headers=((b"content-encoding", b"gzip"),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=64,
                )
            )

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert "gzip" not in str(captured.value)
    assert "compressed-private-body" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_generation_malformed_json_remains_generic_unavailable():
    stream = _RecordingAsyncByteStream((b'{"done":', b"not-json"))
    transport, _requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=64,
                )
            )

    assert stream.closed is True
    assert "not-json" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_generation_cancellation_closes_stream_and_propagates():
    blocked = asyncio.Event()
    release = asyncio.Event()
    body = _generation_response_body("answer")
    stream = _RecordingAsyncByteStream(
        (body[:8], body[8:]),
        blocked=blocked,
        release=release,
    )
    transport, _requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        runtime = OllamaTextGenerationRuntime(
            client,
            10,
            LOCAL_MODEL_ALLOWLIST,
            max_response_bytes=128,
        )
        task = asyncio.create_task(_generate_with_runtime(runtime))
        await blocked.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert stream.iteration_count == 1
    assert stream.closed is True
