from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
from pathlib import Path
import shutil
import struct
import subprocess
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4
import zlib

from fastapi.testclient import TestClient
import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.images import image_dimensions
from app.main import app


_GENERATION_PROMPT = (
    "A simple amber lantern centered on a dark blue table, studio illustration"
)
_EDIT_INSTRUCTION = (
    "Change the amber lantern into a bright blue lantern while preserving composition"
)
_INPAINT_INSTRUCTION = (
    "Replace the masked center with a small green ceramic vase"
)


class _GpuSampler:
    def __init__(self, process_id: int) -> None:
        self._process_id = process_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.max_process_memory_mib = 0
        self.max_utilization_percent = 0

    def start(self) -> None:
        self._sample()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(0.1)

    def _sample(self) -> None:
        try:
            utilization = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            for line in utilization.stdout.splitlines():
                self.max_utilization_percent = max(
                    self.max_utilization_percent,
                    int(line.strip()),
                )
            processes = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,used_gpu_memory",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            for line in processes.stdout.splitlines():
                process_id, separator, memory = line.partition(",")
                if separator and int(process_id.strip()) == self._process_id:
                    self.max_process_memory_mib = max(
                        self.max_process_memory_mib,
                        int(memory.strip()),
                    )
        except (OSError, ValueError, subprocess.SubprocessError):
            pass


def _require_disposable_database() -> None:
    if settings.DATABASE_URL is None and settings.TEST_DATABASE_URL is not None:
        settings.DATABASE_URL = settings.TEST_DATABASE_URL
    database_url = settings.DATABASE_URL
    if database_url is None:
        raise RuntimeError("DATABASE_URL must select the disposable test database")
    parsed = make_url(str(database_url))
    if parsed.host != "127.0.0.1" or parsed.database != "ai_workspace_test":
        raise RuntimeError("real image smoke is restricted to the disposable test DB")


async def _clean_disposable_database() -> None:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    finally:
        await dispose_postgres(engine)


def _provision(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/users",
        headers={"X-User-Provisioning-Token": token},
        json={},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _model_for_images(items: list[dict[str, object]]) -> str:
    matches = [
        item
        for item in items
        if item["installed"]
        and item["runnable_now"]
        and {"image_generation", "image_editing"}.issubset(item["capabilities"])
    ]
    if len(matches) != 1:
        raise RuntimeError("expected one runnable local image model")
    return str(matches[0]["model_id"])


def _png_chunk(kind: bytes, content: bytes) -> bytes:
    return (
        struct.pack(">I", len(content))
        + kind
        + content
        + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
    )


def _inpaint_mask(width: int, height: int) -> bytes:
    rows = bytearray()
    left, right = width // 4, width * 3 // 4
    top, bottom = height // 4, height * 3 // 4
    for y_position in range(height):
        rows.append(0)
        for x_position in range(width):
            masked = (
                left <= x_position < right and top <= y_position < bottom
            )
            value = 255 if masked else 0
            rows.extend((value, value, value))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def _configured_comfy_process() -> tuple[Path, Path, list[str], str]:
    if (
        settings.COMFYUI_BASE_URL is None
        or settings.COMFYUI_CHECKPOINT is None
        or settings.COMFYUI_INPUT_ROOT is None
        or settings.COMFYUI_TEMP_ROOT is None
        or settings.COMFYUI_MODEL_REFERENCE is None
    ):
        raise RuntimeError("the complete pinned ComfyUI runtime must be configured")
    checkpoint = settings.COMFYUI_CHECKPOINT.resolve(strict=True)
    runtime_root = checkpoint.parent.parent.parent
    python = runtime_root / ".venv" / "bin" / "python"
    main = runtime_root / "main.py"
    if not python.is_file() or not main.is_file():
        raise RuntimeError("the configured ComfyUI executable is unavailable")
    parsed = urlsplit(str(settings.COMFYUI_BASE_URL))
    if parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise RuntimeError("the real image smoke requires an IPv4 loopback URL")
    command = [
        str(python),
        str(main),
        "--listen",
        "127.0.0.1",
        "--port",
        str(parsed.port),
        "--lowvram",
        "--reserve-vram",
        "1.5",
        "--cache-none",
        "--preview-method",
        "none",
        "--max-upload-size",
        "11",
        "--disable-metadata",
        "--disable-all-custom-nodes",
        "--disable-api-nodes",
        "--disable-auto-launch",
        "--dont-print-server",
    ]
    return runtime_root, python, command, str(settings.COMFYUI_BASE_URL).rstrip("/")


def _wait_for_comfy(base_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 120.0
    with httpx.Client(timeout=2.0, follow_redirects=False, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("ComfyUI exited before its loopback health check")
            try:
                response = client.get(f"{base_url}/system_stats")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise RuntimeError("ComfyUI did not become ready within its startup bound")


def _start_comfy() -> tuple[subprocess.Popen[bytes], _GpuSampler]:
    runtime_root, _python, command, base_url = _configured_comfy_process()
    try:
        with httpx.Client(
            timeout=1.0, follow_redirects=False, trust_env=False
        ) as client:
            if client.get(f"{base_url}/system_stats").status_code == 200:
                raise RuntimeError("ComfyUI loopback port is already in use")
    except httpx.HTTPError:
        pass
    process = subprocess.Popen(
        command,
        cwd=runtime_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_for_comfy(base_url, process)
    except BaseException:
        _stop_comfy(process, require_clean=False)
        raise
    sampler = _GpuSampler(process.pid)
    sampler.start()
    return process, sampler


def _stop_comfy(
    process: subprocess.Popen[bytes], *, require_clean: bool
) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=30.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10.0)
            if require_clean:
                raise RuntimeError("ComfyUI did not shut down cleanly")


def _download_png(
    client: TestClient, headers: dict[str, str], asset_id: str
) -> bytes:
    downloaded = client.get(f"/api/v1/assets/{asset_id}/content", headers=headers)
    downloaded.raise_for_status()
    if (
        downloaded.headers.get("content-type") != "application/octet-stream"
        or downloaded.headers.get("x-content-type-options") != "nosniff"
        or downloaded.headers.get("x-asset-media-type") != "image/png"
    ):
        raise RuntimeError("generated image MIME was not served safely")
    image_dimensions(downloaded.content, "image/png")
    return downloaded.content


def _runtime_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _create_conversation(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"initial_message": "Prepare a private local image workspace."},
    )
    response.raise_for_status()
    return response.json()["id"]


def _upload_mask(
    client: TestClient,
    headers: dict[str, str],
    content: bytes,
) -> str:
    response = client.post(
        "/api/v1/assets",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("inpaint-mask.png", content, "image/png")},
    )
    response.raise_for_status()
    return response.json()["id"]


def _assert_image_result(
    body: dict[str, object],
    *,
    provenance: str,
    source_asset_id: str | None,
    model_id: str,
) -> str:
    asset = body["asset"]
    message = body["message"]
    if not isinstance(asset, dict) or not isinstance(message, dict):
        raise RuntimeError("image operation response shape changed")
    asset_id = str(asset["id"])
    if (
        body["created"] is not True
        or asset["provenance_kind"] != provenance
        or asset["runtime_id"] != "comfyui"
        or asset["model_id"] != model_id
        or asset["source_asset_id"] != source_asset_id
        or message["content"]
        not in {"Generated an image locally.", "Edited an image locally."}
        or len(message["attachments"]) != 1
        or message["attachments"][0]["id"] != asset_id
    ):
        raise RuntimeError("image operation omitted exact local provenance")
    return asset_id


def main() -> None:
    _require_disposable_database()
    configured_storage_root = settings.ASSET_STORAGE_ROOT
    if configured_storage_root is None:
        raise RuntimeError("ASSET_STORAGE_ROOT must be configured")
    asyncio.run(_clean_disposable_database())
    storage_root = configured_storage_root / f".image-smoke-{uuid4().hex}"
    storage_root.mkdir(mode=0o700)
    settings.ASSET_STORAGE_ROOT = storage_root
    provisioning_token = "i" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()

    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)
    process: subprocess.Popen[bytes] | None = None
    sampler: _GpuSampler | None = None
    asset_ids: list[str] = []
    generated_content = b""
    edited_content = b""
    inpainted_content = b""
    runtime_stopped_cleanly = False
    if settings.COMFYUI_INPUT_ROOT is None or settings.COMFYUI_TEMP_ROOT is None:
        raise RuntimeError("ComfyUI cleanup roots are unavailable")
    input_files_before = _runtime_files(settings.COMFYUI_INPUT_ROOT)
    temp_files_before = _runtime_files(settings.COMFYUI_TEMP_ROOT)

    try:
        process, sampler = _start_comfy()
        with TestClient(app) as client:
            owner_headers = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }
            foreign_headers = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }
            models = client.get("/api/v1/ai/models", headers=owner_headers)
            models.raise_for_status()
            model_id = _model_for_images(models.json()["items"])
            capabilities = client.get(
                "/api/v1/ai/capabilities", headers=owner_headers
            )
            capabilities.raise_for_status()
            capability_states = {
                item["id"]: item["status"]
                for item in capabilities.json()["items"]
            }
            if (
                capability_states.get("image_generation") != "available"
                or capability_states.get("image_editing") != "available"
            ):
                raise RuntimeError("image diagnostics did not advertise real runtimes")
            conversation_id = _create_conversation(client, owner_headers)

            generation_key = str(uuid4())
            generation = client.post(
                "/api/v1/images/generations",
                headers={**owner_headers, "Idempotency-Key": generation_key},
                json={
                    "conversation_id": conversation_id,
                    "model_id": model_id,
                    "prompt": _GENERATION_PROMPT,
                    "negative_prompt": "text, watermark, signature",
                    "width": 768,
                    "height": 768,
                    "steps": 20,
                    "guidance": 7.0,
                    "seed": 20260823,
                },
            )
            generation.raise_for_status()
            generated_id = _assert_image_result(
                generation.json(),
                provenance="image_generation",
                source_asset_id=None,
                model_id=model_id,
            )
            asset_ids.append(generated_id)
            generated_content = _download_png(client, owner_headers, generated_id)

            repeated = client.post(
                "/api/v1/images/generations",
                headers={**owner_headers, "Idempotency-Key": generation_key},
                json={
                    "conversation_id": conversation_id,
                    "model_id": model_id,
                    "prompt": _GENERATION_PROMPT,
                    "negative_prompt": "text, watermark, signature",
                    "width": 768,
                    "height": 768,
                    "steps": 20,
                    "guidance": 7.0,
                    "seed": 20260823,
                },
            )
            if (
                repeated.status_code != 200
                or repeated.json()["created"]
                or repeated.json()["asset"]["id"] != generated_id
            ):
                raise RuntimeError("image generation idempotency was not deterministic")

            edit = client.post(
                "/api/v1/images/edits",
                headers={**owner_headers, "Idempotency-Key": str(uuid4())},
                json={
                    "conversation_id": conversation_id,
                    "model_id": model_id,
                    "source_asset_id": generated_id,
                    "instruction": _EDIT_INSTRUCTION,
                    "negative_prompt": "text, watermark, signature",
                    "steps": 12,
                    "guidance": 7.0,
                    "denoise": 0.75,
                    "seed": 20260824,
                },
            )
            edit.raise_for_status()
            edited_id = _assert_image_result(
                edit.json(),
                provenance="image_editing",
                source_asset_id=generated_id,
                model_id=model_id,
            )
            asset_ids.append(edited_id)
            edited_content = _download_png(client, owner_headers, edited_id)
            if edited_id == generated_id or edited_content == generated_content:
                raise RuntimeError("img2img did not create a distinct edited asset")
            if _download_png(client, owner_headers, generated_id) != generated_content:
                raise RuntimeError("img2img changed the original asset")

            dimensions = image_dimensions(generated_content, "image/png")
            mask_content = _inpaint_mask(dimensions.width, dimensions.height)
            mask_id = _upload_mask(client, owner_headers, mask_content)
            asset_ids.append(mask_id)
            inpaint = client.post(
                "/api/v1/images/edits",
                headers={**owner_headers, "Idempotency-Key": str(uuid4())},
                json={
                    "conversation_id": conversation_id,
                    "model_id": model_id,
                    "source_asset_id": generated_id,
                    "mask_asset_id": mask_id,
                    "instruction": _INPAINT_INSTRUCTION,
                    "negative_prompt": "text, watermark, signature",
                    "steps": 12,
                    "guidance": 7.0,
                    "denoise": 0.9,
                    "seed": 20260825,
                },
            )
            inpaint.raise_for_status()
            inpainted_id = _assert_image_result(
                inpaint.json(),
                provenance="image_editing",
                source_asset_id=generated_id,
                model_id=model_id,
            )
            asset_ids.append(inpainted_id)
            inpainted_content = _download_png(client, owner_headers, inpainted_id)
            if (
                inpainted_id in {generated_id, edited_id}
                or inpainted_content in {generated_content, edited_content}
            ):
                raise RuntimeError("inpainting did not create a distinct edited asset")
            if _download_png(client, owner_headers, generated_id) != generated_content:
                raise RuntimeError("inpainting changed the original asset")

            foreign_edit = client.post(
                "/api/v1/images/edits",
                headers={**foreign_headers, "Idempotency-Key": str(uuid4())},
                json={
                    "conversation_id": conversation_id,
                    "model_id": model_id,
                    "source_asset_id": generated_id,
                    "instruction": "unauthorized edit",
                },
            )
            if foreign_edit.status_code != 404:
                raise RuntimeError("foreign owner could edit an owned image")
            for asset_id in asset_ids:
                if client.get(
                    f"/api/v1/assets/{asset_id}/content", headers=foreign_headers
                ).status_code != 404:
                    raise RuntimeError("foreign owner could read local image bytes")

            for asset_id in (inpainted_id, edited_id, mask_id, generated_id):
                if client.delete(
                    f"/api/v1/assets/{asset_id}", headers=foreign_headers
                ).status_code != 404:
                    raise RuntimeError("foreign owner could delete a local image")
                if client.delete(
                    f"/api/v1/assets/{asset_id}", headers=owner_headers
                ).status_code != 204:
                    raise RuntimeError("owned local image deletion failed")
                if client.get(
                    f"/api/v1/assets/{asset_id}/content", headers=owner_headers
                ).status_code != 404:
                    raise RuntimeError("deleted local image remained readable")
            messages = client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers=owner_headers,
            )
            messages.raise_for_status()
            tombstones = {
                attachment["id"]: attachment
                for message in messages.json()["items"]
                for attachment in message["attachments"]
            }
            for asset_id in (generated_id, edited_id, inpainted_id):
                tombstone = tombstones.get(asset_id)
                if tombstone is None or tombstone != {
                    "id": asset_id,
                    "position": 1,
                    "state": "deleted",
                    "original_filename": None,
                    "media_type": None,
                    "byte_size": None,
                    "provenance_kind": None,
                    "source_asset_id": None,
                }:
                    raise RuntimeError("deleted local image metadata was not redacted")

        sampler.stop()
        _stop_comfy(process, require_clean=True)
        runtime_stopped_cleanly = True
        if sampler.max_process_memory_mib < 2_048:
            raise RuntimeError("ComfyUI did not provide NVIDIA process-memory evidence")
        if sampler.max_utilization_percent <= 0:
            raise RuntimeError("ComfyUI did not use the NVIDIA GPU")

        logs = captured_logs.getvalue()
        encoded_images = {
            base64.b64encode(content).decode("ascii")
            for content in (generated_content, edited_content, inpainted_content)
        }
        if (
            any(value in logs for value in encoded_images)
            or _GENERATION_PROMPT in logs
            or _EDIT_INSTRUCTION in logs
            or _INPAINT_INSTRUCTION in logs
            or str(storage_root) in logs
        ):
            raise RuntimeError("private image data or a storage path reached logs")
        stored_files = {
            path.relative_to(storage_root)
            for path in storage_root.rglob("*")
            if path.is_file()
        }
        if stored_files:
            raise RuntimeError("image smoke left owned asset bytes behind")
        if (
            _runtime_files(settings.COMFYUI_INPUT_ROOT) != input_files_before
            or _runtime_files(settings.COMFYUI_TEMP_ROOT) != temp_files_before
        ):
            raise RuntimeError("image smoke left ComfyUI input or temp artifacts")
    finally:
        if sampler is not None:
            sampler.stop()
        if process is not None and not runtime_stopped_cleanly:
            _stop_comfy(process, require_clean=False)
        logging.getLogger().removeHandler(handler)
        handler.close()
        try:
            asyncio.run(_clean_disposable_database())
        finally:
            settings.ASSET_STORAGE_ROOT = configured_storage_root
            if (
                storage_root.parent.resolve() != configured_storage_root.resolve()
                or not storage_root.name.startswith(".image-smoke-")
            ):
                raise RuntimeError("image smoke storage cleanup target is unsafe")
            shutil.rmtree(storage_root)

    if sampler is None:  # pragma: no cover - startup guard
        raise RuntimeError("ComfyUI GPU sampler was unavailable")
    print("REAL_COMFYUI_IMAGE_GENERATION=passed")
    print("REAL_COMFYUI_IMG2IMG=passed")
    print("REAL_COMFYUI_INPAINTING=passed")
    print(f"COMFYUI_GPU_MEMORY_MIB={sampler.max_process_memory_mib}")
    print(f"COMFYUI_GPU_UTILIZATION_PERCENT={sampler.max_utilization_percent}")
    print("AUTHENTICATED_IMAGE_OWNER_ISOLATION=passed")
    print("IMAGE_PROVENANCE_REDACTION_AND_CLEANUP=passed")
    print("COMFYUI_CLEAN_SHUTDOWN=passed")


if __name__ == "__main__":
    main()
