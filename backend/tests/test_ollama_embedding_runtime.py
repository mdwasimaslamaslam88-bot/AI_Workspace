import asyncio
import json

import httpx
import pytest

from app.runtimes.ollama_embedding import (
    OllamaEmbeddingRuntime,
    OllamaEmbeddingUnavailableError,
)


@pytest.mark.asyncio
async def test_embeds_a_bounded_batch_with_exact_local_model_and_unloads():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["encoding"] = request.headers["accept-encoding"]
        captured["payload"] = json.loads(await request.aread())
        return httpx.Response(
            200,
            json={
                "model": "nomic-embed-text:latest",
                "embeddings": [[3.0, 4.0], [0.0, 2.0]],
            },
        )

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await OllamaEmbeddingRuntime(
            client,
            "nomic-embed-text:latest",
        ).embed_texts(("first", "second"))

    assert captured == {
        "path": "/api/embed",
        "encoding": "identity",
        "payload": {
            "model": "nomic-embed-text:latest",
            "input": ["first", "second"],
            "truncate": False,
            "keep_alive": 0,
        },
    }
    assert len(result) == 2
    assert result[0].model_id == "ollama:nomic-embed-text:latest"
    assert result[0].dimensions == 2
    assert result[0].norm == 1.0
    assert len(result[0].packed) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {},
        {"embeddings": []},
        {"embeddings": [[1.0], [2.0]]},
        {"embeddings": [[True, 1.0]]},
        {"embeddings": [[float("nan"), 1.0]]},
        {"embeddings": [[0.0, 0.0]]},
        {"model": "different:latest", "embeddings": [[1.0, 2.0]]},
    ],
)
async def test_rejects_malformed_or_unsafe_runtime_output(response):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    ) as client:
        runtime = OllamaEmbeddingRuntime(client, "nomic-embed-text:latest")
        with pytest.raises(OllamaEmbeddingUnavailableError):
            await runtime.embed_texts(("one",))


@pytest.mark.asyncio
async def test_request_and_response_bounds_fail_closed():
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"Content-Length": "1000"},
            content=b"{}",
        )

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    ) as client:
        request_bounded = OllamaEmbeddingRuntime(
            client,
            "nomic-embed-text:latest",
            max_request_bytes=1,
        )
        with pytest.raises(OllamaEmbeddingUnavailableError):
            await request_bounded.embed_texts(("one",))
        assert calls == 0

        response_bounded = OllamaEmbeddingRuntime(
            client,
            "nomic-embed-text:latest",
            max_response_bytes=10,
        )
        with pytest.raises(OllamaEmbeddingUnavailableError):
            await response_bounded.embed_texts(("one",))
        assert calls == 1


@pytest.mark.asyncio
async def test_cancellation_releases_runtime_admission():
    entered = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""  # pragma: no cover

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=BlockingStream())

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    ) as client:
        runtime = OllamaEmbeddingRuntime(client, "nomic-embed-text:latest")
        task = asyncio.create_task(runtime.embed_texts(("one",)))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime._admission._value == 1


@pytest.mark.parametrize(
    "reference",
    ["", " leading", "trailing ", "x" * 241],
)
def test_embedding_model_reference_must_be_exact(reference):
    client = httpx.AsyncClient(base_url="http://127.0.0.1:11434")
    try:
        with pytest.raises(ValueError):
            OllamaEmbeddingRuntime(client, reference)
    finally:
        asyncio.run(client.aclose())
