from __future__ import annotations

import asyncio
import base64
import hashlib
from io import BytesIO, StringIO
import logging
from pathlib import Path
import re
import shutil
import subprocess
import threading
from uuid import uuid4
import wave

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.main import app
from scripts.runtime_smoke_safety import select_disposable_runtime_database


_SPOKEN_TEXT = (
    "Testing local speech. The color is amber. The object is a lantern."
)
_TRANSCRIPT_CHECKPOINT_GROUPS = (
    (frozenset({"testing", "local", "speech"}), 2),
    (frozenset({"color", "amber"}), 1),
    (frozenset({"object", "lantern"}), 1),
)
_MIN_TRANSCRIPT_CHECKPOINT_TERMS = 5


def _contains_spoken_checkpoint(transcript: str) -> bool:
    words = frozenset(re.findall(r"[a-z]+", transcript.lower()))
    matched = set().union(
        *(words.intersection(group) for group, _minimum in _TRANSCRIPT_CHECKPOINT_GROUPS)
    )
    return (
        len(matched) >= _MIN_TRANSCRIPT_CHECKPOINT_TERMS
        and all(
            len(words.intersection(group)) >= minimum
            for group, minimum in _TRANSCRIPT_CHECKPOINT_GROUPS
        )
    )


class _GpuSampler:
    def __init__(self, process_fragment: str) -> None:
        self._process_fragment = process_fragment.lower()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.baseline_memory_mib = 0
        self.max_gpu_memory_mib = 0
        self.max_gpu_utilization_percent = 0
        self.max_process_memory_mib = 0

    def start(self) -> None:
        self._sample_gpu(set_baseline=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample_gpu(set_baseline=False)
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-compute-apps=process_name,used_gpu_memory",
                        "--format=csv,noheader,nounits",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                for line in result.stdout.splitlines():
                    name, separator, memory = line.rpartition(",")
                    if separator and self._process_fragment in name.lower():
                        self.max_process_memory_mib = max(
                            self.max_process_memory_mib,
                            int(memory.strip()),
                        )
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self._stop.wait(0.1)

    def _sample_gpu(self, *, set_baseline: bool) -> None:
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
                if not separator:
                    continue
                memory_value = int(memory.strip())
                utilization_value = int(utilization.strip())
                if set_baseline:
                    self.baseline_memory_mib = memory_value
                self.max_gpu_memory_mib = max(
                    self.max_gpu_memory_mib, memory_value
                )
                self.max_gpu_utilization_percent = max(
                    self.max_gpu_utilization_percent, utilization_value
                )
        except (OSError, ValueError, subprocess.SubprocessError):
            pass


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


def _provision(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/users",
        headers={"X-User-Provisioning-Token": token},
        json={},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _model_for_capability(items: list[dict[str, object]], capability: str) -> str:
    matches = [
        item
        for item in items
        if item["installed"]
        and item["runnable_now"]
        and capability in item["capabilities"]
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one runnable local {capability} model")
    return str(matches[0]["model_id"])


def _validate_wav(content: bytes) -> tuple[int, int, float]:
    try:
        with wave.open(BytesIO(content), "rb") as audio:
            rate = audio.getframerate()
            channels = audio.getnchannels()
            duration = audio.getnframes() / rate
    except (EOFError, wave.Error) as exc:
        raise RuntimeError("local speech synthesis did not return a valid WAV") from exc
    if channels not in {1, 2} or not 8_000 <= rate <= 48_000 or duration <= 0:
        raise RuntimeError("local speech synthesis WAV metadata is unsafe")
    return rate, channels, duration


def _stored_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def main() -> None:
    _require_disposable_database()
    if settings.STT_DEVICE != "cuda":
        raise RuntimeError(
            "real voice smoke requires configured CUDA speech recognition"
        )
    configured_storage_root = settings.ASSET_STORAGE_ROOT
    if configured_storage_root is None:
        raise RuntimeError("ASSET_STORAGE_ROOT must be configured")
    asyncio.run(_clean_disposable_database())
    storage_root = configured_storage_root / f".voice-smoke-{uuid4().hex}"
    storage_root.mkdir(mode=0o700)
    settings.ASSET_STORAGE_ROOT = storage_root

    provisioning_token = "s" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    text_logs = logging.StreamHandler()
    log_text = StringIO()
    text_logs.setStream(log_text)
    logging.getLogger().addHandler(text_logs)
    synthesized_id: str | None = None
    upload_id: str | None = None
    gpu_sampler = _GpuSampler("python")

    try:
        with TestClient(app) as client:
            owner_headers = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }
            foreign_headers = {
                "Authorization": f"Bearer {_provision(client, provisioning_token)}"
            }
            models_response = client.get("/api/v1/ai/models", headers=owner_headers)
            models_response.raise_for_status()
            models = models_response.json()["items"]
            stt_model_id = _model_for_capability(models, "speech_recognition")
            tts_model_id = _model_for_capability(models, "speech_synthesis")
            capabilities = client.get(
                "/api/v1/ai/capabilities", headers=owner_headers
            )
            capabilities.raise_for_status()
            capability_states = {
                item["id"]: item["status"]
                for item in capabilities.json()["items"]
            }
            if (
                capability_states.get("voice_input") != "available"
                or capability_states.get("voice_output") != "available"
            ):
                raise RuntimeError(
                    "voice diagnostics did not advertise real runtimes"
                )

            idempotency_key = str(uuid4())
            synthesized = client.post(
                "/api/v1/voice/syntheses",
                headers={**owner_headers, "Idempotency-Key": idempotency_key},
                json={"model_id": tts_model_id, "text": _SPOKEN_TEXT},
            )
            synthesized.raise_for_status()
            synthesized_body = synthesized.json()
            synthesized_id = synthesized_body["asset"]["id"]
            if (
                not synthesized_body["created"]
                or synthesized_body["asset"]["provenance_kind"]
                != "speech_synthesis"
                or synthesized_body["asset"]["runtime_id"] != "piper"
                or synthesized_body["asset"]["model_id"] != tts_model_id
                or synthesized_body["asset"]["source_asset_id"] is not None
            ):
                raise RuntimeError("synthesized audio omitted exact local provenance")
            if (
                str(storage_root) in synthesized.text
                or _SPOKEN_TEXT in synthesized.text
            ):
                raise RuntimeError("voice API exposed private input or a storage path")

            repeated = client.post(
                "/api/v1/voice/syntheses",
                headers={**owner_headers, "Idempotency-Key": idempotency_key},
                json={"model_id": tts_model_id, "text": _SPOKEN_TEXT},
            )
            if (
                repeated.status_code != 200
                or repeated.json()["created"]
                or repeated.json()["asset"]["id"] != synthesized_id
            ):
                raise RuntimeError("speech synthesis idempotency was not deterministic")

            downloaded = client.get(
                f"/api/v1/assets/{synthesized_id}/content",
                headers=owner_headers,
            )
            downloaded.raise_for_status()
            rate, channels, duration = _validate_wav(downloaded.content)
            if (
                downloaded.headers.get("content-type")
                != "application/octet-stream"
                or downloaded.headers.get("x-content-type-options") != "nosniff"
                or downloaded.headers.get("x-asset-media-type") != "audio/wav"
            ):
                raise RuntimeError("synthesized audio MIME was not served safely")
            if client.get(
                f"/api/v1/assets/{synthesized_id}/content",
                headers=foreign_headers,
            ).status_code != 404:
                raise RuntimeError("foreign owner could read synthesized audio")

            uploaded = client.post(
                "/api/v1/assets",
                headers={**owner_headers, "Idempotency-Key": str(uuid4())},
                files={
                    "file": (
                        "voice-checkpoint.wav",
                        downloaded.content,
                        "audio/wav",
                    )
                },
            )
            uploaded.raise_for_status()
            upload_body = uploaded.json()
            upload_id = upload_body["id"]
            if (
                upload_body["provenance_kind"] != "upload"
                or upload_body["runtime_id"] is not None
                or upload_body["model_id"] is not None
            ):
                raise RuntimeError(
                    "recorded speech upload claimed generated provenance"
                )

            gpu_sampler.start()
            try:
                transcription = client.post(
                    "/api/v1/voice/transcriptions",
                    headers=owner_headers,
                    json={"asset_id": upload_id, "model_id": stt_model_id},
                )
            finally:
                gpu_sampler.stop()
            transcription.raise_for_status()
            transcript = transcription.json()["text"].lower()
            if not _contains_spoken_checkpoint(transcript):
                raise RuntimeError(
                    "real CUDA transcription missed its spoken checkpoint"
                )
            if (
                gpu_sampler.max_process_memory_mib <= 0
                and gpu_sampler.max_gpu_memory_mib
                < gpu_sampler.baseline_memory_mib + 512
            ):
                raise RuntimeError(
                    "speech recognition did not provide NVIDIA GPU evidence"
                )
            if gpu_sampler.max_gpu_utilization_percent <= 0:
                raise RuntimeError("speech recognition did not use the NVIDIA GPU")

            if client.post(
                "/api/v1/voice/transcriptions",
                headers=foreign_headers,
                json={"asset_id": upload_id, "model_id": stt_model_id},
            ).status_code != 404:
                raise RuntimeError("foreign owner could transcribe owned audio")
            for asset_id in (synthesized_id, upload_id):
                if client.delete(
                    f"/api/v1/assets/{asset_id}", headers=foreign_headers
                ).status_code != 404:
                    raise RuntimeError("foreign owner could delete voice audio")
                if client.delete(
                    f"/api/v1/assets/{asset_id}", headers=owner_headers
                ).status_code != 204:
                    raise RuntimeError("owned voice audio deletion failed")
                if client.get(
                    f"/api/v1/assets/{asset_id}/content", headers=owner_headers
                ).status_code != 404:
                    raise RuntimeError("deleted voice audio remained readable")

        logs = log_text.getvalue()
        encoded_audio = base64.b64encode(downloaded.content).decode("ascii")
        if (
            _SPOKEN_TEXT.lower() in logs.lower()
            or transcript in logs.lower()
            or encoded_audio in logs
            or str(storage_root) in logs
        ):
            raise RuntimeError("private voice data or a storage path reached logs")
        if _stored_files(storage_root):
            raise RuntimeError("voice smoke left generated asset bytes behind")
    finally:
        logging.getLogger().removeHandler(text_logs)
        text_logs.close()
        try:
            asyncio.run(_clean_disposable_database())
        finally:
            settings.ASSET_STORAGE_ROOT = configured_storage_root
            if (
                storage_root.parent.resolve() != configured_storage_root.resolve()
                or not storage_root.name.startswith(".voice-smoke-")
            ):
                raise RuntimeError("voice smoke storage cleanup target is unsafe")
            shutil.rmtree(storage_root)

    print("REAL_PIPER_TTS=passed")
    print(f"TTS_WAV={rate}Hz/{channels}ch/{duration:.3f}s")
    print("REAL_FASTER_WHISPER_STT=passed")
    print(f"STT_GPU_MEMORY_MIB={gpu_sampler.max_gpu_memory_mib}")
    print(f"STT_GPU_UTILIZATION_PERCENT={gpu_sampler.max_gpu_utilization_percent}")
    print("AUTHENTICATED_VOICE_OWNER_ISOLATION=passed")
    print("VOICE_LOG_REDACTION_AND_CLEANUP=passed")


if __name__ == "__main__":
    main()
