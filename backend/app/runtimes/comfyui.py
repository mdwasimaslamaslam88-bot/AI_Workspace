from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
import unicodedata
from uuid import uuid4

import httpx

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelModality,
    RuntimeModel,
)
from app.images import (
    MAX_IMAGE_OUTPUT_BYTES,
    GeneratedImage,
    ImageRuntimeInputError,
    ImageRuntimeUnavailableError,
    image_dimensions,
    sanitize_generated_png,
)


MAX_COMFY_JSON_RESPONSE_BYTES = 1_048_576
MAX_COMFY_ERROR_RESPONSE_BYTES = 16_384
MAX_IMAGE_PROMPT_CHARACTERS = 2_000
MAX_NEGATIVE_PROMPT_CHARACTERS = 1_000
MAX_IMAGE_RUNTIME_SECONDS = 600.0
_CHECKPOINT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,199}\.safetensors$")
_COMFY_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,239}$")


def _require_directory(path: Path, name: str) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{name} must be a directory")
    return resolved


def _require_file(path: Path, name: str) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{name} must be a regular file")
    return resolved


def _validate_prompt(value: str, *, maximum: int, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not value.strip() or len(value) > maximum:
        raise ImageRuntimeInputError(f"{name} is outside its bound")
    if any(
        unicodedata.category(character).startswith("C")
        and character not in {"\n", "\t"}
        for character in value
    ):
        raise ImageRuntimeInputError(f"{name} contains control data")
    return value


def _validate_generation_options(
    *, width: int, height: int, steps: int, guidance: float, seed: int
) -> None:
    for name, value in (("width", width), ("height", height)):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 512 <= value <= 1_024
            or value % 64
        ):
            raise ImageRuntimeInputError(f"image {name} is outside its bound")
    if width * height > 1_048_576:
        raise ImageRuntimeInputError("image pixel count is outside its bound")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 30:
        raise ImageRuntimeInputError("image steps are outside their bound")
    if (
        isinstance(guidance, bool)
        or not isinstance(guidance, (int, float))
        or not math.isfinite(guidance)
        or not 1 <= guidance <= 10
    ):
        raise ImageRuntimeInputError("image guidance is outside its bound")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ImageRuntimeInputError("image seed is outside its bound")


class ComfyUIImageRuntime:
    runtime_id = "comfyui"
    supports_reference_selector = True

    def __init__(
        self,
        client: httpx.AsyncClient,
        checkpoint: Path,
        input_root: Path,
        temp_root: Path,
        *,
        model_reference: str,
        timeout_seconds: float = 300.0,
        max_active: int = 1,
        required_vram_bytes: int = 9 * 1024**3,
        required_ram_bytes: int = 16 * 1024**3,
    ) -> None:
        if not isinstance(client, httpx.AsyncClient):
            raise TypeError("ComfyUI client must be an httpx AsyncClient")
        if not isinstance(model_reference, str) or not model_reference.strip():
            raise ValueError("ComfyUI model reference must be nonblank")
        if len(model_reference) > 240:
            raise ValueError("ComfyUI model reference is too long")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= MAX_IMAGE_RUNTIME_SECONDS
        ):
            raise ValueError("ComfyUI timeout is outside its bound")
        if isinstance(max_active, bool) or not isinstance(max_active, int):
            raise TypeError("ComfyUI concurrency must be an integer")
        if max_active != 1:
            raise ValueError("ComfyUI concurrency must remain one per process")
        for name, value in (
            ("required VRAM", required_vram_bytes),
            ("required RAM", required_ram_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"ComfyUI {name} must be positive")

        self.client = client
        self.checkpoint = _require_file(checkpoint, "ComfyUI checkpoint")
        if _CHECKPOINT_PATTERN.fullmatch(self.checkpoint.name) is None:
            raise ValueError("ComfyUI checkpoint name is unsafe")
        self.input_root = _require_directory(input_root, "ComfyUI input root")
        self.temp_root = _require_directory(temp_root, "ComfyUI temp root")
        self.model_reference = model_reference
        self.timeout_seconds = float(timeout_seconds)
        self.required_vram_bytes = required_vram_bytes
        self.required_ram_bytes = required_ram_bytes
        self._admission = asyncio.Semaphore(max_active)

    async def discover_models(
        self,
        *,
        reference_selector: Callable[[str], bool] | None = None,
    ) -> tuple[RuntimeModel, ...]:
        if reference_selector is not None and not reference_selector(
            self.model_reference
        ):
            return ()
        installed = self.checkpoint.is_file()
        available = False
        try:
            stats, loader = await asyncio.gather(
                self._json_request("GET", "/system_stats"),
                self._json_request(
                    "GET", "/object_info/CheckpointLoaderSimple"
                ),
            )
            available = (
                installed
                and self._has_cuda_device(stats)
                and self._has_checkpoint(loader)
            )
        except ImageRuntimeUnavailableError:
            available = False
        return (
            RuntimeModel(
                reference=self.model_reference,
                display_name="Stable Diffusion XL Base 1.0",
                modality=ModelModality.IMAGE,
                family="Stable Diffusion XL",
                parameter_class="3.5B",
                capabilities=(
                    ModelCapability.IMAGE_GENERATION,
                    ModelCapability.IMAGE_EDITING,
                ),
                quantization="FP16",
                estimated_vram_bytes=self.required_vram_bytes,
                availability=(
                    ModelAvailability.AVAILABLE
                    if available
                    else ModelAvailability.UNAVAILABLE
                ),
                required_vram_bytes=self.required_vram_bytes,
                required_ram_bytes=self.required_ram_bytes,
                installed=installed,
            ),
        )

    async def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        guidance: float,
        seed: int,
    ) -> GeneratedImage:
        prompt = _validate_prompt(
            prompt, maximum=MAX_IMAGE_PROMPT_CHARACTERS, name="image prompt"
        )
        negative_prompt = self._validate_negative_prompt(negative_prompt)
        _validate_generation_options(
            width=width,
            height=height,
            steps=steps,
            guidance=guidance,
            seed=seed,
        )
        graph = self._generation_graph(
            prompt,
            negative_prompt,
            width=width,
            height=height,
            steps=steps,
            guidance=float(guidance),
            seed=seed,
        )
        content = await self._execute_graph(graph, output_node="7")
        return GeneratedImage(content, width, height)

    async def edit(
        self,
        source: bytes,
        source_media_type: str,
        instruction: str,
        *,
        negative_prompt: str,
        mask: bytes | None,
        mask_media_type: str | None,
        steps: int,
        guidance: float,
        denoise: float,
        seed: int,
    ) -> GeneratedImage:
        source_dimensions = image_dimensions(source, source_media_type)
        if (
            source_dimensions.width < 512
            or source_dimensions.height < 512
            or source_dimensions.width > 1_024
            or source_dimensions.height > 1_024
            or source_dimensions.width % 64
            or source_dimensions.height % 64
        ):
            raise ImageRuntimeInputError(
                "source image dimensions are outside the edit bound"
            )
        instruction = _validate_prompt(
            instruction,
            maximum=MAX_IMAGE_PROMPT_CHARACTERS,
            name="image edit instruction",
        )
        negative_prompt = self._validate_negative_prompt(negative_prompt)
        _validate_generation_options(
            width=source_dimensions.width,
            height=source_dimensions.height,
            steps=steps,
            guidance=guidance,
            seed=seed,
        )
        if (
            isinstance(denoise, bool)
            or not isinstance(denoise, (int, float))
            or not math.isfinite(denoise)
            or not 0.1 <= denoise <= 1.0
        ):
            raise ImageRuntimeInputError("image edit denoise is outside its bound")
        if (mask is None) != (mask_media_type is None):
            raise ImageRuntimeInputError("image edit mask metadata is incomplete")
        if mask is not None and mask_media_type is not None:
            mask_dimensions = image_dimensions(mask, mask_media_type)
            if mask_dimensions != source_dimensions:
                raise ImageRuntimeInputError("image edit mask dimensions changed")

        uploaded: list[tuple[Path, str]] = []
        try:
            source_name, source_path = await self._upload_image(
                source, source_media_type
            )
            uploaded.append((source_path, source_name))
            mask_name = None
            if mask is not None and mask_media_type is not None:
                mask_name, mask_path = await self._upload_image(
                    mask, mask_media_type
                )
                uploaded.append((mask_path, mask_name))
            graph, output_node = self._edit_graph(
                source_name,
                mask_name,
                instruction,
                negative_prompt,
                steps=steps,
                guidance=float(guidance),
                denoise=float(denoise),
                seed=seed,
            )
            content = await self._execute_graph(graph, output_node=output_node)
            return GeneratedImage(
                content, source_dimensions.width, source_dimensions.height
            )
        finally:
            for path, _name in uploaded:
                await asyncio.to_thread(self._delete_owned_runtime_file, path)

    @staticmethod
    def _validate_negative_prompt(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("negative prompt must be text")
        if len(value) > MAX_NEGATIVE_PROMPT_CHARACTERS:
            raise ImageRuntimeInputError("negative prompt is outside its bound")
        if any(
            unicodedata.category(character).startswith("C")
            and character not in {"\n", "\t"}
            for character in value
        ):
            raise ImageRuntimeInputError("negative prompt contains control data")
        return value

    def _generation_graph(
        self,
        prompt: str,
        negative_prompt: str,
        *,
        width: int,
        height: int,
        steps: int,
        guidance: float,
        seed: int,
    ) -> dict[str, Any]:
        return {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self.checkpoint.name},
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["1", 1]},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative_prompt, "clip": ["1", 1]},
            },
            "4": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "5": self._sampler_inputs(
                latent_node="4",
                positive_node="2",
                negative_node="3",
                steps=steps,
                guidance=guidance,
                denoise=1.0,
                seed=seed,
            ),
            "6": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
            },
            "7": {"class_type": "PreviewImage", "inputs": {"images": ["6", 0]}},
        }

    def _edit_graph(
        self,
        source_name: str,
        mask_name: str | None,
        instruction: str,
        negative_prompt: str,
        *,
        steps: int,
        guidance: float,
        denoise: float,
        seed: int,
    ) -> tuple[dict[str, Any], str]:
        graph: dict[str, Any] = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self.checkpoint.name},
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": instruction, "clip": ["1", 1]},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative_prompt, "clip": ["1", 1]},
            },
            "4": {"class_type": "LoadImage", "inputs": {"image": source_name}},
        }
        if mask_name is None:
            graph["5"] = {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["4", 0], "vae": ["1", 2]},
            }
            latent_node = "5"
            sampler_node = "6"
        else:
            graph["5"] = {
                "class_type": "LoadImageMask",
                "inputs": {"image": mask_name, "channel": "red"},
            }
            graph["6"] = {
                "class_type": "VAEEncodeForInpaint",
                "inputs": {
                    "pixels": ["4", 0],
                    "vae": ["1", 2],
                    "mask": ["5", 0],
                    "grow_mask_by": 8,
                },
            }
            latent_node = "6"
            sampler_node = "7"
        graph[sampler_node] = self._sampler_inputs(
            latent_node=latent_node,
            positive_node="2",
            negative_node="3",
            steps=steps,
            guidance=guidance,
            denoise=denoise,
            seed=seed,
        )
        decode_node = str(int(sampler_node) + 1)
        output_node = str(int(sampler_node) + 2)
        graph[decode_node] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [sampler_node, 0], "vae": ["1", 2]},
        }
        graph[output_node] = {
            "class_type": "PreviewImage",
            "inputs": {"images": [decode_node, 0]},
        }
        return graph, output_node

    @staticmethod
    def _sampler_inputs(
        *,
        latent_node: str,
        positive_node: str,
        negative_node: str,
        steps: int,
        guidance: float,
        denoise: float,
        seed: int,
    ) -> dict[str, Any]:
        return {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": seed,
                "steps": steps,
                "cfg": guidance,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "positive": [positive_node, 0],
                "negative": [negative_node, 0],
                "latent_image": [latent_node, 0],
                "denoise": denoise,
            },
        }

    async def _execute_graph(
        self, graph: Mapping[str, Any], *, output_node: str
    ) -> bytes:
        prompt_id = str(uuid4())
        output_path: Path | None = None
        submitted = False
        async with self._admission:
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    queued = await self._json_request(
                        "POST",
                        "/prompt",
                        payload={"prompt": graph, "prompt_id": prompt_id},
                    )
                    if queued.get("prompt_id") != prompt_id:
                        raise ImageRuntimeUnavailableError(
                            "ComfyUI changed the submitted job identity"
                        )
                    submitted = True
                    image = await self._wait_for_image(prompt_id, output_node)
                    content, output_path = await self._download_image(image)
                    return content
            except asyncio.CancelledError:
                if submitted:
                    await self._best_effort_cancel(prompt_id)
                raise
            except TimeoutError:
                if submitted:
                    await self._best_effort_cancel(prompt_id)
                raise
            except ImageRuntimeUnavailableError:
                if submitted:
                    await self._best_effort_cancel(prompt_id)
                raise
            except Exception as exc:
                if submitted:
                    await self._best_effort_cancel(prompt_id)
                raise ImageRuntimeUnavailableError(
                    "ComfyUI image operation is unavailable"
                ) from exc
            finally:
                if output_path is not None:
                    await asyncio.to_thread(
                        self._delete_owned_runtime_file, output_path
                    )
                if submitted:
                    await self._best_effort_post(
                        "/history", {"delete": [prompt_id]}
                    )
                await self._best_effort_post(
                    "/free", {"unload_models": True, "free_memory": True}
                )

    async def _wait_for_image(
        self, prompt_id: str, output_node: str
    ) -> Mapping[str, Any]:
        while True:
            history = await self._json_request("GET", f"/history/{prompt_id}")
            record = history.get(prompt_id)
            if isinstance(record, dict):
                outputs = record.get("outputs")
                node = outputs.get(output_node) if isinstance(outputs, dict) else None
                images = node.get("images") if isinstance(node, dict) else None
                if isinstance(images, list) and len(images) == 1:
                    image = images[0]
                    if isinstance(image, dict):
                        return image
                status_value = record.get("status")
                if isinstance(status_value, dict) and status_value.get("completed"):
                    raise ImageRuntimeUnavailableError(
                        "ComfyUI completed without one bounded image"
                    )
            await asyncio.sleep(0.25)

    async def _download_image(
        self, image: Mapping[str, Any]
    ) -> tuple[bytes, Path]:
        filename = image.get("filename")
        subfolder = image.get("subfolder", "")
        image_type = image.get("type")
        if (
            not isinstance(filename, str)
            or not isinstance(subfolder, str)
            or image_type != "temp"
        ):
            raise ImageRuntimeUnavailableError("ComfyUI output identity is invalid")
        path = self._safe_runtime_path(self.temp_root, subfolder, filename)
        content = await self._bytes_request(
            "GET",
            "/view",
            params={
                "filename": filename,
                "subfolder": subfolder,
                "type": "temp",
            },
            maximum=MAX_IMAGE_OUTPUT_BYTES,
        )
        return sanitize_generated_png(content), path

    async def _upload_image(
        self, content: bytes, media_type: str
    ) -> tuple[str, Path]:
        extension = "png" if media_type == "image/png" else "jpg"
        requested_name = f"ai-workspace-{uuid4().hex}.{extension}"
        requested_path = self._safe_runtime_path(
            self.input_root, "", requested_name
        )
        accepted = False
        try:
            response = await self._json_request(
                "POST",
                "/upload/image",
                files={"image": (requested_name, content, media_type)},
                form={"type": "input", "overwrite": "false"},
                maximum=MAX_COMFY_ERROR_RESPONSE_BYTES,
            )
            name = response.get("name")
            subfolder = response.get("subfolder", "")
            response_type = response.get("type")
            if (
                name != requested_name
                or not isinstance(subfolder, str)
                or subfolder
                or response_type != "input"
            ):
                raise ImageRuntimeUnavailableError(
                    "ComfyUI upload identity changed"
                )
            accepted = True
            return name, requested_path
        finally:
            if not accepted:
                await asyncio.to_thread(
                    self._delete_owned_runtime_file, requested_path
                )

    async def _json_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        form: Mapping[str, str] | None = None,
        maximum: int = MAX_COMFY_JSON_RESPONSE_BYTES,
    ) -> dict[str, Any]:
        content = await self._bytes_request(
            method,
            path,
            json_payload=payload,
            files=files,
            form=form,
            maximum=maximum,
        )
        try:
            decoded = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImageRuntimeUnavailableError(
                "ComfyUI returned malformed JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise ImageRuntimeUnavailableError("ComfyUI returned malformed JSON")
        return decoded

    async def _bytes_request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Mapping[str, Any] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        form: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        maximum: int,
    ) -> bytes:
        body = bytearray()
        try:
            async with self.client.stream(
                method,
                path,
                headers={"Accept-Encoding": "identity"},
                json=json_payload,
                files=files,
                data=form,
                params=params,
                timeout=self.timeout_seconds,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != (
                    "identity"
                ):
                    raise ImageRuntimeUnavailableError(
                        "ComfyUI response encoding is unsupported"
                    )
                declared = response.headers.get("content-length")
                if declared is not None and (
                    not declared.isdigit() or int(declared) > maximum
                ):
                    raise ImageRuntimeUnavailableError(
                        "ComfyUI response exceeded its bound"
                    )
                async for chunk in response.aiter_bytes():
                    if len(chunk) > maximum - len(body):
                        raise ImageRuntimeUnavailableError(
                            "ComfyUI response exceeded its bound"
                        )
                    body.extend(chunk)
            return bytes(body)
        except asyncio.CancelledError:
            raise
        except ImageRuntimeUnavailableError:
            raise
        except Exception as exc:
            raise ImageRuntimeUnavailableError(
                "ComfyUI request is unavailable"
            ) from exc
        finally:
            body.clear()

    async def _best_effort_cancel(self, prompt_id: str) -> None:
        await self._best_effort_post("/queue", {"delete": [prompt_id]})
        await self._best_effort_post("/interrupt", {"prompt_id": prompt_id})

    async def _best_effort_post(
        self, path: str, payload: Mapping[str, Any]
    ) -> None:
        operation = asyncio.create_task(
            self._bytes_request(
                "POST",
                path,
                json_payload=payload,
                maximum=MAX_COMFY_ERROR_RESPONSE_BYTES,
            )
        )
        try:
            async with asyncio.timeout(3.0):
                await asyncio.shield(operation)
        except BaseException:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)

    def _has_checkpoint(self, payload: Mapping[str, Any]) -> bool:
        node = payload.get("CheckpointLoaderSimple")
        inputs = node.get("input") if isinstance(node, dict) else None
        required = inputs.get("required") if isinstance(inputs, dict) else None
        ckpt = required.get("ckpt_name") if isinstance(required, dict) else None
        names = ckpt[0] if isinstance(ckpt, list) and ckpt else None
        return isinstance(names, list) and self.checkpoint.name in names

    @staticmethod
    def _has_cuda_device(payload: Mapping[str, Any]) -> bool:
        devices = payload.get("devices")
        return isinstance(devices, list) and any(
            isinstance(device, dict)
            and device.get("type") == "cuda"
            and isinstance(device.get("vram_total"), int)
            and device["vram_total"] > 0
            for device in devices
        )

    @staticmethod
    def _safe_runtime_path(root: Path, subfolder: str, filename: str) -> Path:
        if _COMFY_FILENAME_PATTERN.fullmatch(filename) is None:
            raise ImageRuntimeUnavailableError("ComfyUI filename is unsafe")
        relative = PurePosixPath(subfolder)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            if subfolder:
                raise ImageRuntimeUnavailableError("ComfyUI subfolder is unsafe")
        path = root.joinpath(*relative.parts, filename)
        resolved_parent = path.parent.resolve(strict=False)
        if resolved_parent != root and root not in resolved_parent.parents:
            raise ImageRuntimeUnavailableError("ComfyUI path escaped its root")
        return path

    @staticmethod
    def _delete_owned_runtime_file(path: Path) -> None:
        try:
            details = path.lstat()
            if not stat.S_ISREG(details.st_mode):
                return
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or (
                    opened.st_dev,
                    opened.st_ino,
                ) != (details.st_dev, details.st_ino):
                    return
            finally:
                os.close(descriptor)
            path.unlink()
        except OSError:
            return
