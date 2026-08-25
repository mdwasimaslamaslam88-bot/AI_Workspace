from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import shutil
import subprocess
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.main import app
from scripts.runtime_smoke_safety import select_disposable_runtime_database


_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082"
)


class _GpuSampler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.max_memory_mib = 0
        self.max_utilization_percent = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                for line in result.stdout.splitlines():
                    memory, separator, utilization = line.partition(",")
                    if separator:
                        self.max_memory_mib = max(
                            self.max_memory_mib, int(memory.strip())
                        )
                        self.max_utilization_percent = max(
                            self.max_utilization_percent,
                            int(utilization.strip()),
                        )
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self._stop.wait(0.2)


def _require_disposable_database() -> None:
    select_disposable_runtime_database(settings)


async def _clean_disposable_database() -> None:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    finally:
        await dispose_postgres(engine)


def _provision(client: TestClient, provisioning_token: str) -> str:
    response = client.post(
        "/api/v1/users",
        headers={"X-User-Provisioning-Token": provisioning_token},
        json={},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def main() -> None:
    _require_disposable_database()
    if settings.OLLAMA_BASE_URL is None:
        raise RuntimeError("OLLAMA_BASE_URL must be configured")
    configured_storage_root = settings.ASSET_STORAGE_ROOT
    if configured_storage_root is None:
        raise RuntimeError("ASSET_STORAGE_ROOT must be configured")
    asyncio.run(_clean_disposable_database())
    storage_root = configured_storage_root / f".vision-smoke-{uuid4().hex}"
    storage_root.mkdir(mode=0o700)
    settings.ASSET_STORAGE_ROOT = storage_root

    provisioning_token = "v" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    encoded_png = base64.b64encode(_PNG).decode("ascii")
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)
    asset_id: str | None = None

    try:
        with TestClient(app) as client:
            owner_token = _provision(client, provisioning_token)
            foreign_token = _provision(client, provisioning_token)
            owner_headers = {"Authorization": f"Bearer {owner_token}"}
            foreign_headers = {"Authorization": f"Bearer {foreign_token}"}

            models = client.get("/api/v1/ai/models", headers=owner_headers)
            models.raise_for_status()
            vision_models = [
                item
                for item in models.json()["items"]
                if item["installed"]
                and item["runnable_now"]
                and "text_generation" in item["capabilities"]
                and "vision_input" in item["capabilities"]
            ]
            if not vision_models:
                raise RuntimeError("no installed runnable allowlisted vision model")
            model_id = vision_models[0]["model_id"]

            conversation = client.post(
                "/api/v1/conversations",
                headers=owner_headers,
                json={"initial_message": "Prepare for the real PNG vision smoke."},
            )
            conversation.raise_for_status()
            conversation_id = conversation.json()["id"]

            uploaded = client.post(
                "/api/v1/assets",
                headers={
                    **owner_headers,
                    "Idempotency-Key": str(uuid4()),
                },
                files={"file": ("vision-smoke.png", _PNG, "image/png")},
            )
            uploaded.raise_for_status()
            asset_id = uploaded.json()["id"]
            if uploaded.json()["media_type"] != "image/png":
                raise RuntimeError("PNG upload was not safely recognized")

            downloaded = client.get(
                f"/api/v1/assets/{asset_id}/content",
                headers=owner_headers,
            )
            downloaded.raise_for_status()
            if downloaded.content != _PNG:
                raise RuntimeError("owned PNG loopback changed bytes")

            if client.get(
                f"/api/v1/assets/{asset_id}/content",
                headers=foreign_headers,
            ).status_code != 404:
                raise RuntimeError("foreign owner could read the PNG")

            gpu_sampler = _GpuSampler()
            gpu_sampler.start()
            try:
                generated = client.post(
                    f"/api/v1/conversations/{conversation_id}/messages/generate",
                    headers=owner_headers,
                    json={
                        "model_id": model_id,
                        "user_message": "What is the dominant color of this image? Reply briefly.",
                        "attachment_ids": [asset_id],
                        "max_output_tokens": 32,
                        "temperature": 0,
                    },
                )
            finally:
                gpu_sampler.stop()
            generated.raise_for_status()
            if (
                gpu_sampler.max_memory_mib < 2_048
                or gpu_sampler.max_utilization_percent <= 0
            ):
                raise RuntimeError("vision runtime did not provide NVIDIA GPU evidence")
            assistant_content = generated.json()["message"]["content"]
            if "red" not in assistant_content.lower():
                raise RuntimeError("vision runtime did not identify the real PNG")

            messages = client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers=owner_headers,
            )
            messages.raise_for_status()
            persisted = messages.json()["items"]
            attached = [item for item in persisted if item["attachments"]]
            if len(attached) != 1 or attached[0]["attachments"][0]["id"] != asset_id:
                raise RuntimeError("only the owned attachment relation was not persisted")
            for item in persisted:
                if encoded_png in item["content"] or str(storage_root) in item["content"]:
                    raise RuntimeError("image bytes or a storage path reached Message.content")

            if client.delete(
                f"/api/v1/assets/{asset_id}",
                headers=foreign_headers,
            ).status_code != 404:
                raise RuntimeError("foreign owner could delete the PNG")
            deleted = client.delete(
                f"/api/v1/assets/{asset_id}",
                headers=owner_headers,
            )
            if deleted.status_code != 204:
                raise RuntimeError("owned PNG deletion failed")
            if client.get(
                f"/api/v1/assets/{asset_id}/content",
                headers=owner_headers,
            ).status_code != 404:
                raise RuntimeError("deleted PNG remained downloadable")
            tombstones = client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers=owner_headers,
            )
            tombstones.raise_for_status()
            deleted_relation = [
                attachment
                for item in tombstones.json()["items"]
                for attachment in item["attachments"]
            ][0]
            if deleted_relation != {
                "id": asset_id,
                "position": 1,
                "state": "deleted",
                "original_filename": None,
                "media_type": None,
                "byte_size": None,
                "provenance_kind": None,
                "source_asset_id": None,
            }:
                raise RuntimeError("deleted PNG did not become a safe tombstone")

        logs = captured_logs.getvalue()
        if encoded_png in logs or str(storage_root) in logs:
            raise RuntimeError("image bytes or a storage path reached logs")
        if assistant_content.strip() and assistant_content in logs:
            raise RuntimeError("the raw model response reached logs")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        try:
            asyncio.run(_clean_disposable_database())
        finally:
            settings.ASSET_STORAGE_ROOT = configured_storage_root
            if (
                storage_root.parent.resolve() != configured_storage_root.resolve()
                or not storage_root.name.startswith(".vision-smoke-")
            ):
                raise RuntimeError("vision smoke storage cleanup target is unsafe")
            shutil.rmtree(storage_root)

    print("REAL_VISION_SMOKE=passed")
    print("AUTHENTICATED_OWNER_ISOLATION=passed")
    print("MESSAGE_AND_LOG_REDACTION=passed")
    print("ASSET_TOMBSTONE=passed")
    print(f"OLLAMA_GPU_MEMORY_MIB={gpu_sampler.max_memory_mib}")
    print(f"OLLAMA_GPU_UTILIZATION_PERCENT={gpu_sampler.max_utilization_percent}")


if __name__ == "__main__":
    main()
