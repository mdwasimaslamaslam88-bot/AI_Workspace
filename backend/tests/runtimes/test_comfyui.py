import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
import zlib

import httpx
import pytest

from app.ai.catalog import ModelAvailability, ModelCapability
from app.images import ImageRuntimeInputError, ImageRuntimeUnavailableError
from app.runtimes.comfyui import (
    MAX_COMFY_JSON_RESPONSE_BYTES,
    ComfyUIImageRuntime,
)


def _png(width: int = 512, height: int = 512) -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + name
            + data
            + (zlib.crc32(name + data) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    header = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"bounded fixture"))
        + chunk(b"IEND", b"")
    )


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    checkpoint = tmp_path / "sd_xl_base_1.0.safetensors"
    checkpoint.write_bytes(b"fixture")
    input_root = tmp_path / "input"
    temp_root = tmp_path / "temp"
    input_root.mkdir()
    temp_root.mkdir()
    return checkpoint, input_root, temp_root


def _runtime(
    client: httpx.AsyncClient, tmp_path: Path
) -> ComfyUIImageRuntime:
    checkpoint, input_root, temp_root = _paths(tmp_path)
    return ComfyUIImageRuntime(
        client,
        checkpoint,
        input_root,
        temp_root,
        model_reference="stabilityai/sdxl-base@pinned",
    )


@pytest.mark.asyncio
async def test_comfyui_discovery_requires_cuda_and_exact_registered_checkpoint(
    tmp_path, monkeypatch
):
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8188") as client:
        runtime = _runtime(client, tmp_path)

        async def response(_method, path, **_kwargs):
            if path == "/system_stats":
                return {"devices": [{"type": "cuda", "vram_total": 12_000}]}
            return {
                "CheckpointLoaderSimple": {
                    "input": {
                        "required": {
                            "ckpt_name": [[runtime.checkpoint.name], {}]
                        }
                    }
                }
            }

        monkeypatch.setattr(runtime, "_json_request", response)

        model = (await runtime.discover_models())[0]

    assert model.availability is ModelAvailability.AVAILABLE
    assert model.installed is True
    assert model.capabilities == (
        ModelCapability.IMAGE_EDITING,
        ModelCapability.IMAGE_GENERATION,
    )
    assert model.required_vram_bytes == 9 * 1024**3


@pytest.mark.asyncio
async def test_comfyui_generation_submits_only_fixed_bounded_graph(
    tmp_path, monkeypatch
):
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8188") as client:
        runtime = _runtime(client, tmp_path)
        execute = AsyncMock(return_value=_png())
        monkeypatch.setattr(runtime, "_execute_graph", execute)

        result = await runtime.generate(
            "A private watercolor lighthouse",
            negative_prompt="unsafe artifacts",
            width=512,
            height=512,
            steps=20,
            guidance=7.0,
            seed=17,
        )

    graph = execute.await_args.args[0]
    assert result.content == _png()
    assert {node["class_type"] for node in graph.values()} == {
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
        "EmptyLatentImage",
        "KSampler",
        "VAEDecode",
        "PreviewImage",
    }
    assert graph["1"]["inputs"]["ckpt_name"] == runtime.checkpoint.name
    assert graph["5"]["inputs"]["steps"] == 20
    assert execute.await_args.kwargs == {"output_node": "7"}


@pytest.mark.asyncio
async def test_comfyui_img2img_and_inpaint_clean_uploaded_inputs(
    tmp_path, monkeypatch
):
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8188") as client:
        runtime = _runtime(client, tmp_path)
        uploaded: list[Path] = []

        async def upload(content, _media_type):
            name = f"input-{len(uploaded)}.png"
            path = runtime.input_root / name
            path.write_bytes(content)
            uploaded.append(path)
            return name, path

        execute = AsyncMock(return_value=_png())
        monkeypatch.setattr(runtime, "_upload_image", upload)
        monkeypatch.setattr(runtime, "_execute_graph", execute)

        img2img = await runtime.edit(
            _png(),
            "image/png",
            "Make the lighthouse blue",
            negative_prompt="",
            mask=None,
            mask_media_type=None,
            steps=18,
            guidance=6.0,
            denoise=0.55,
            seed=21,
        )
        img2img_graph = execute.await_args_list[0].args[0]
        inpaint = await runtime.edit(
            _png(),
            "image/png",
            "Replace only the white mask area",
            negative_prompt="",
            mask=_png(),
            mask_media_type="image/png",
            steps=18,
            guidance=6.0,
            denoise=0.8,
            seed=22,
        )
        inpaint_graph = execute.await_args_list[1].args[0]

    assert img2img.width == inpaint.width == 512
    assert img2img_graph["5"]["class_type"] == "VAEEncode"
    assert any(
        node["class_type"] == "VAEEncodeForInpaint"
        for node in inpaint_graph.values()
    )
    assert uploaded and all(not path.exists() for path in uploaded)


@pytest.mark.asyncio
async def test_comfyui_cancellation_targets_job_and_runs_cleanup(
    tmp_path, monkeypatch
):
    history_started = asyncio.Event()
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8188") as client:
        runtime = _runtime(client, tmp_path)

        async def request(_method, path, *, payload=None, **_kwargs):
            if path == "/prompt":
                return {"prompt_id": payload["prompt_id"]}
            history_started.set()
            await asyncio.Future()

        cancel = AsyncMock()
        cleanup = AsyncMock()
        monkeypatch.setattr(runtime, "_json_request", request)
        monkeypatch.setattr(runtime, "_best_effort_cancel", cancel)
        monkeypatch.setattr(runtime, "_best_effort_post", cleanup)
        operation = asyncio.create_task(
            runtime._execute_graph({}, output_node="7")
        )
        await history_started.wait()

        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation

    cancel.assert_awaited_once()
    assert {call.args[0] for call in cleanup.await_args_list} == {
        "/history",
        "/free",
    }


@pytest.mark.asyncio
async def test_comfyui_rejects_oversized_response_before_buffering(tmp_path):
    async def handler(_request):
        return httpx.Response(
            200,
            headers={"Content-Length": str(MAX_COMFY_JSON_RESPONSE_BYTES + 1)},
            content=b"{}",
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8188", transport=transport
    ) as client:
        runtime = _runtime(client, tmp_path)
        with pytest.raises(ImageRuntimeUnavailableError):
            await runtime._json_request("GET", "/system_stats")


def test_comfyui_rejects_path_traversal_and_unbounded_generation(tmp_path):
    checkpoint, input_root, temp_root = _paths(tmp_path)
    with pytest.raises(ImageRuntimeUnavailableError):
        ComfyUIImageRuntime._safe_runtime_path(
            temp_root, "../outside", "output.png"
        )

    async def check_generation():
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8188") as client:
            runtime = ComfyUIImageRuntime(
                client,
                checkpoint,
                input_root,
                temp_root,
                model_reference="stabilityai/sdxl-base@pinned",
            )
            with pytest.raises(ImageRuntimeInputError):
                await runtime.generate(
                    "prompt",
                    negative_prompt="",
                    width=2_048,
                    height=2_048,
                    steps=20,
                    guidance=7.0,
                    seed=1,
                )

    asyncio.run(check_generation())
