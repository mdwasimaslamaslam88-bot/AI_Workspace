from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
from typing import Any

import httpx

from app.ai.catalog import _public_model_id
from app.ai.routing import ModelTask, task_system_instruction
from scripts.ai_benchmark_cases import BenchmarkCase, build_text_matrix
from scripts.ai_quality_benchmark import _evaluate_answer
from scripts.code_generation_benchmark import (
    CodeGenerationCase,
    build_code_generation_cases,
    verify_generated_code,
)


MODEL_REFERENCES = (
    "qwen3:8b",
    "qwen2.5-coder:7b",
    "qwen2.5-coder:14b-instruct-q3_K_L",
)
EXPERIMENT_SEED = 20260827
BASELINE_PROFILE = "baseline"
QWEN3_THINKING_AUTO_PROFILE = "qwen3_thinking_auto"
EXPERIMENT_PROFILES = frozenset(
    {BASELINE_PROFILE, QWEN3_THINKING_AUTO_PROFILE}
)
MAX_GPU_TEMPERATURE_C = 85
MIN_AVAILABLE_RAM_BYTES = 8 * 1024**3
MAX_MODEL_VRAM_BYTES = 11 * 1024**3
BASE_SYSTEM_PROMPT = (
    "You are WORK STATION under an objective benchmark. Follow the user's "
    "explicit output contract. Do not reveal hidden reasoning, credentials, "
    "private paths, or unrelated content."
)


class BenchmarkResourceGuardError(Exception):
    """The isolated benchmark crossed a host safety threshold."""


@dataclass(frozen=True, slots=True)
class ComparisonCase:
    category: str
    task: ModelTask
    case: BenchmarkCase


def build_comparison_cases() -> tuple[ComparisonCase, ...]:
    source = build_text_matrix()
    groups: tuple[tuple[str, ModelTask, frozenset[str]], ...] = (
        (
            "coding",
            ModelTask.CODING,
            frozenset({"coding", "advanced_coding"}),
        ),
        (
            "debugging",
            ModelTask.DEBUGGING,
            frozenset({"debugging", "difficult_debugging"}),
        ),
        (
            "reasoning",
            ModelTask.REASONING,
            frozenset(
                {
                    "simple_reasoning",
                    "multi_step_reasoning",
                    "algorithm_reasoning",
                    "systems_reasoning",
                    "contradiction_detection",
                }
            ),
        ),
        (
            "mathematics",
            ModelTask.REASONING,
            frozenset(
                {
                    "arithmetic",
                    "complex_math",
                    "algebra_reasoning",
                    "probability_reasoning",
                    "statistics_reasoning",
                    "discrete_math",
                }
            ),
        ),
        (
            "expert_analysis",
            ModelTask.EXPERT_ANALYSIS,
            frozenset(
                case.category
                for case in source
                if case.metadata.get("routing_task") == "expert_analysis"
            ),
        ),
        (
            "long_context",
            ModelTask.LONG_CONTEXT,
            frozenset({"long_context_reasoning"}),
        ),
    )
    selected: list[ComparisonCase] = []
    for group_name, task, categories in groups:
        selected.extend(
            ComparisonCase(group_name, task, case)
            for case in source
            if case.category in categories
        )

    exact_categories = frozenset(
        {
            "instruction_following",
            "structured_data",
            "contradictory_instructions",
            "misleading_context",
            "conflicting_documents",
            "impossible_request",
            "prompt_injection",
            "tool_misuse",
            "unauthorized_data",
            "owner_isolation",
            "malicious_file",
            "fabricated_citation",
            "missing_information",
            "arbitrary_code",
        }
    )
    selected.extend(
        ComparisonCase("exact_output", ModelTask.EXACT_OUTPUT, case)
        for case in source
        if case.category in exact_categories
    )
    selected.extend(
        (
            ComparisonCase(
                "exact_output",
                ModelTask.EXACT_OUTPUT,
                BenchmarkCase(
                    test_id="exact-output-base-case",
                    category="exact_output",
                    difficulty="expert",
                    prompt=(
                        "A recursive function has no terminating condition. "
                        "What is the standard recursion term for the missing "
                        "condition? Name it only."
                    ),
                    expected_behavior="Return the canonical term only.",
                    exact="base case",
                    max_output_tokens=24,
                ),
            ),
            ComparisonCase(
                "exact_output",
                ModelTask.EXACT_OUTPUT,
                BenchmarkCase(
                    test_id="exact-output-recovered",
                    category="exact_output",
                    difficulty="expert",
                    prompt="Reply exactly RECOVERED.",
                    expected_behavior="Return the requested token only.",
                    exact="RECOVERED",
                    max_output_tokens=16,
                ),
            ),
        )
    )
    return tuple(selected)


def comparison_cases_for_profile(profile: str) -> tuple[ComparisonCase, ...]:
    cases = build_comparison_cases()
    if profile == BASELINE_PROFILE:
        return cases
    if profile == QWEN3_THINKING_AUTO_PROFILE:
        return tuple(item for item in cases if item.category == "exact_output")
    raise ValueError("unsupported model experiment profile")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return ordered[rank]


def _routing_decision() -> dict[str, Any]:
    return {
        "review_status": "complete",
        "candidate_production_admission": "rejected",
        "candidate_reason": (
            "The 14B candidate won zero complete categories and tied the 7B "
            "coder at 16/24 executable artifacts. Its lower latency in some "
            "quality-losing categories did not offset the whole-category "
            "quality regressions."
        ),
        "production_allowlist_change": False,
        "production_route_changes": {
            "exact_output": {
                "from": "qwen2.5-coder:7b",
                "to": "qwen3:8b",
                "evidence": "97.65 versus 80.97 across all 34 cases",
            }
        },
        "unchanged_routes": {
            "coding": "qwen2.5-coder:7b",
            "debugging": "qwen3:8b",
            "reasoning": "qwen3:8b",
            "mathematics": "qwen3:8b",
            "expert_analysis": "qwen3:8b",
            "code_generation": "qwen3:8b",
        },
        "long_context_note": (
            "The 7B coder's 0.75-point advantage inside the isolated "
            "three-model comparison was below the material quality threshold "
            "and did not justify a production route change. The existing "
            "hardware-aware catalog route remains authoritative for long "
            "context and still excludes models below the request's required "
            "token count."
        ),
        "inference_profile": {
            "selected": "thinking_disabled for exact_output/code_generation",
            "rejected": "qwen3_thinking_auto",
            "evidence": (
                "Thinking-auto scored 97.09 exact and 16/24 executable code "
                "versus 97.65 and 19/24 with thinking disabled, with materially "
                "higher latency."
            ),
        },
    }


def _read_memory_snapshot() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, separator, raw_value = line.partition(":")
        if separator and key in {"MemTotal", "MemAvailable"}:
            values[key] = int(raw_value.strip().split()[0]) * 1024
    if set(values) != {"MemTotal", "MemAvailable"}:
        raise RuntimeError("system memory inventory is unavailable")
    return {
        "total_bytes": values["MemTotal"],
        "available_bytes": values["MemAvailable"],
        "used_bytes": values["MemTotal"] - values["MemAvailable"],
    }


def _read_gpu_snapshot() -> dict[str, Any]:
    completed = subprocess.run(
        (
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,memory.free,"
            "utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("GPU inventory is unavailable")
    parts = [part.strip() for part in completed.stdout.strip().split(",")]
    if len(parts) != 7:
        raise RuntimeError("GPU inventory returned an invalid row")
    return {
        "name": parts[0],
        "total_mib": int(parts[1]),
        "used_mib": int(parts[2]),
        "free_mib": int(parts[3]),
        "utilization_percent": int(parts[4]),
        "temperature_c": int(parts[5]),
        "power_watts": float(parts[6]),
    }


def _installed_model_blob_verification(model_reference: str) -> dict[str, Any]:
    shown = subprocess.run(
        ("ollama", "show", model_reference, "--modelfile"),
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout
    match = re.search(r"^FROM\s+(\S*[/\\]sha256-([0-9a-f]{64}))\s*$", shown, re.MULTILINE)
    if match is None:
        raise RuntimeError("installed candidate model blob identity is unavailable")
    blob_path = Path(match.group(1))
    expected_digest = match.group(2)
    if (
        not blob_path.is_absolute()
        or blob_path.parent.name != "blobs"
        or blob_path.is_symlink()
        or not blob_path.is_file()
    ):
        raise RuntimeError("installed candidate model blob path is unsafe")
    digest = hashlib.sha256()
    with blob_path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    actual_digest = digest.hexdigest()
    if actual_digest != expected_digest:
        raise RuntimeError("installed candidate model blob integrity check failed")
    return {
        "model_reference": model_reference,
        "algorithm": "SHA-256",
        "expected_digest": expected_digest,
        "actual_digest": actual_digest,
        "verified": True,
        "blob_size_bytes": blob_path.stat().st_size,
        "private_path_recorded": False,
    }


class CandidateBenchmarkRunner:
    def __init__(
        self,
        api_origin: str,
        provisioning_token: str,
        model_reference: str,
        output_path: Path,
        profile: str = BASELINE_PROFILE,
    ) -> None:
        self.api_origin = api_origin.rstrip("/")
        self.provisioning_token = provisioning_token
        self.model_reference = model_reference
        self.model_id = _public_model_id("ollama-local", model_reference)
        self.output_path = output_path
        self.profile = profile
        self.owner_token = ""
        self.client = httpx.Client(
            base_url=self.api_origin,
            timeout=httpx.Timeout(300.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
        )
        self.ollama = httpx.Client(
            base_url="http://127.0.0.1:11434",
            timeout=httpx.Timeout(10.0, connect=2.0),
            follow_redirects=False,
            trust_env=False,
        )
        self.results: list[dict[str, Any]] = []
        self.resource_samples: list[dict[str, Any]] = []
        self.model_metadata: dict[str, Any] = {}
        self.started_at = time.time()

    @property
    def owner_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.owner_token}"}

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.request(method, path, **kwargs)

    def initialize(self) -> None:
        live = self._request("GET", "/api/v1/health/live")
        live.raise_for_status()
        provision = self._request(
            "POST",
            "/api/v1/users",
            headers={"X-User-Provisioning-Token": self.provisioning_token},
            json={},
        )
        provision.raise_for_status()
        self.owner_token = provision.json()["access_token"]
        models = self._request(
            "GET",
            "/api/v1/ai/models",
            headers=self.owner_headers,
        )
        models.raise_for_status()
        items = models.json()["items"]
        matching = [item for item in items if item["model_id"] == self.model_id]
        if len(matching) != 1:
            raise RuntimeError("isolated candidate model is absent from the catalog")
        self.model_metadata = matching[0]
        if not self.model_metadata.get("installed") or not self.model_metadata.get(
            "runnable_now"
        ):
            raise RuntimeError("isolated candidate model is not hardware admitted")
        other_text_models = [
            item
            for item in items
            if item["model_id"] != self.model_id
            and "text_generation" in item.get("capabilities", [])
        ]
        if other_text_models:
            raise RuntimeError("isolated benchmark exposed another text model")
        self._sample_resources()

    def _sample_resources(self) -> None:
        gpu = _read_gpu_snapshot()
        memory = _read_memory_snapshot()
        ps = self.ollama.get("/api/ps")
        ps.raise_for_status()
        model_process = next(
            (
                item
                for item in ps.json().get("models", [])
                if item.get("model", item.get("name")) == self.model_reference
            ),
            None,
        )
        sample = {
            "captured_at_unix": round(time.time(), 3),
            "gpu": gpu,
            "ram": memory,
            "ollama_model": (
                {
                    "size_bytes": model_process.get("size") or 0,
                    "size_vram_bytes": model_process.get("size_vram") or 0,
                    "context_length": model_process.get("context_length"),
                }
                if model_process is not None
                else None
            ),
        }
        if gpu["temperature_c"] >= MAX_GPU_TEMPERATURE_C:
            raise BenchmarkResourceGuardError("GPU thermal safety guard triggered")
        if memory["available_bytes"] < MIN_AVAILABLE_RAM_BYTES:
            raise BenchmarkResourceGuardError("RAM safety guard triggered")
        if (
            model_process is not None
            and (model_process.get("size_vram") or 0) > MAX_MODEL_VRAM_BYTES
        ):
            raise BenchmarkResourceGuardError("model VRAM safety guard triggered")
        self.resource_samples.append(sample)

    def _generate(
        self,
        case: BenchmarkCase,
        task: ModelTask,
        *,
        prompt: str | None = None,
        max_output_tokens: int | None = None,
    ) -> tuple[str, float, str]:
        effective_prompt = prompt or case.prompt
        profile_instruction = (
            task_system_instruction(task)
            if self.profile == QWEN3_THINKING_AUTO_PROFILE
            else None
        )
        create = self._request(
            "POST",
            "/api/v1/conversations",
            headers=self.owner_headers,
            json={
                "title": f"Candidate benchmark {case.test_id}",
                "system_prompt": "\n\n".join(
                    item
                    for item in (BASE_SYSTEM_PROMPT, profile_instruction)
                    if item
                ),
                "initial_message": effective_prompt,
            },
        )
        create.raise_for_status()
        conversation_id = create.json()["id"]
        started = time.perf_counter()
        generated = self._request(
            "POST",
            f"/api/v1/conversations/{conversation_id}/messages/generate",
            headers=self.owner_headers,
            json={
                **(
                    {"model_id": self.model_id}
                    if self.profile == QWEN3_THINKING_AUTO_PROFILE
                    else {"task": task.value}
                ),
                "max_output_tokens": max_output_tokens or case.max_output_tokens,
                "temperature": 0.0,
                "seed": EXPERIMENT_SEED,
            },
        )
        latency = time.perf_counter() - started
        generated.raise_for_status()
        payload = generated.json()
        if payload["model_id"] != self.model_id:
            raise RuntimeError("isolated task router selected an unexpected model")
        answer = payload["message"]["content"]
        if not isinstance(answer, str):
            raise RuntimeError("generation returned an invalid answer")
        self._sample_resources()
        return answer, latency, payload["model_id"]

    def run_text_cases(self) -> None:
        cases = comparison_cases_for_profile(self.profile)
        for index, comparison in enumerate(cases, 1):
            try:
                answer, latency, model_id = self._generate(
                    comparison.case,
                    comparison.task,
                )
                evaluation = _evaluate_answer(comparison.case, answer, latency)
                request_error = None
            except (httpx.HTTPError, KeyError) as exc:
                answer = ""
                latency = 0.0
                model_id = self.model_id
                evaluation = {
                    "score": 0.0,
                    "result": "FAIL",
                    "dimensions": {},
                    "failure_reason": f"generation_request_failed:{type(exc).__name__}",
                    "hallucination": False,
                    "checks": [],
                    "word_count": 0,
                }
                request_error = type(exc).__name__
            record = {
                "test_id": comparison.case.test_id,
                "comparison_category": comparison.category,
                "source_category": comparison.case.category,
                "task": comparison.task.value,
                "prompt": comparison.case.prompt,
                "expected_behavior": comparison.case.expected_behavior,
                "actual_answer": answer,
                "score": evaluation["score"],
                "result": evaluation["result"],
                "latency_seconds": round(latency, 4),
                "failure_reason": evaluation["failure_reason"],
                "dimensions": evaluation["dimensions"],
                "hallucination": evaluation["hallucination"],
                "model_id": model_id,
                "request_error": request_error,
                "retry_result": None,
            }
            if record["result"] != "PASS":
                record["retry_result"] = self._retry_text(comparison)
            self.results.append(record)
            if index % 10 == 0 or index == len(cases):
                print(
                    f"MODEL_CANDIDATE_TEXT_PROGRESS={index}/{len(cases)}",
                    flush=True,
                )

    def _retry_text(self, comparison: ComparisonCase) -> dict[str, Any]:
        try:
            answer, latency, model_id = self._generate(
                comparison.case,
                comparison.task,
            )
            evaluation = _evaluate_answer(comparison.case, answer, latency)
            return {
                "actual_answer": answer,
                "score": evaluation["score"],
                "result": evaluation["result"],
                "latency_seconds": round(latency, 4),
                "failure_reason": evaluation["failure_reason"],
                "model_id": model_id,
                "identical_prompt": True,
            }
        except (httpx.HTTPError, KeyError) as exc:
            return {
                "actual_answer": "",
                "score": 0.0,
                "result": "FAIL",
                "latency_seconds": 0.0,
                "failure_reason": f"retry_request_failed:{type(exc).__name__}",
                "model_id": self.model_id,
                "identical_prompt": True,
            }

    def run_code_cases(self) -> None:
        repository_root = Path(__file__).parents[2]
        cases = build_code_generation_cases(repository_root)
        for index, code_case in enumerate(cases, 1):
            benchmark_case = BenchmarkCase(
                test_id=code_case.test_id,
                category="code_generation",
                difficulty="expert",
                prompt=code_case.prompt,
                expected_behavior=(
                    "Generate an original artifact that passes independent syntax, "
                    "type, safety, and execution checks without examiner repair."
                ),
                max_output_tokens=1_024,
            )
            record = self._run_code_case(code_case, benchmark_case)
            self.results.append(record)
            print(
                f"MODEL_CANDIDATE_CODE_PROGRESS={index}/{len(cases)}",
                flush=True,
            )

    def _run_code_case(
        self,
        code_case: CodeGenerationCase,
        benchmark_case: BenchmarkCase,
    ) -> dict[str, Any]:
        try:
            answer, latency, model_id = self._generate(
                benchmark_case,
                ModelTask.CODE_GENERATION,
            )
            verification = verify_generated_code(code_case, answer)
            request_error = None
        except (httpx.HTTPError, KeyError) as exc:
            answer = ""
            latency = 0.0
            model_id = self.model_id
            verification = {
                "passed": False,
                "failure_reason": f"generation_request_failed:{type(exc).__name__}",
                "evidence": [],
            }
            request_error = type(exc).__name__
        passed = verification.get("passed") is True
        retry_result = None
        if not passed:
            retry_result = self._retry_code(code_case, benchmark_case)
        return {
            "test_id": code_case.test_id,
            "comparison_category": "executable_code_generation",
            "source_category": "code_generation",
            "language": code_case.language,
            "task": ModelTask.CODE_GENERATION.value,
            "prompt": code_case.prompt,
            "expected_behavior": benchmark_case.expected_behavior,
            "actual_answer": answer,
            "score": 100.0 if passed else 0.0,
            "result": "PASS" if passed else "FAIL",
            "latency_seconds": round(latency, 4),
            "failure_reason": verification.get("failure_reason"),
            "model_id": model_id,
            "request_error": request_error,
            "tool_evidence": verification.get("evidence", []),
            "static_safety_passed": verification.get("static_safety_passed", True),
            "original_answer_scored_before_execution": True,
            "examiner_repaired_artifact": False,
            "retry_result": retry_result,
        }

    def _retry_code(
        self,
        code_case: CodeGenerationCase,
        benchmark_case: BenchmarkCase,
    ) -> dict[str, Any]:
        attempts: dict[str, Any] = {}
        prompts = {
            "identical": code_case.prompt,
            "diagnostic_variant": (
                code_case.prompt
                + "\nDiagnostic variant: Re-check every stated edge case, input "
                "rejection, module/export shape, and exact output constraint. "
                "Return one complete original artifact only."
            ),
        }
        for name, prompt in prompts.items():
            try:
                answer, latency, model_id = self._generate(
                    benchmark_case,
                    ModelTask.CODE_GENERATION,
                    prompt=prompt,
                )
                verification = verify_generated_code(code_case, answer)
                attempts[name] = {
                    "prompt": prompt if name == "diagnostic_variant" else None,
                    "actual_answer": answer,
                    "result": "PASS" if verification.get("passed") is True else "FAIL",
                    "latency_seconds": round(latency, 4),
                    "failure_reason": verification.get("failure_reason"),
                    "model_id": model_id,
                    "tool_evidence": verification.get("evidence", []),
                }
            except (httpx.HTTPError, KeyError) as exc:
                attempts[name] = {
                    "prompt": prompt if name == "diagnostic_variant" else None,
                    "actual_answer": "",
                    "result": "FAIL",
                    "latency_seconds": 0.0,
                    "failure_reason": f"retry_request_failed:{type(exc).__name__}",
                    "model_id": self.model_id,
                    "tool_evidence": [],
                }
        attempts["deterministic_failure"] = all(
            attempts[name]["result"] == "FAIL" for name in prompts
        )
        return attempts

    def _summary(self) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for result in self.results:
            grouped[result["comparison_category"]].append(result)
        categories = {}
        for category, items in sorted(grouped.items()):
            latencies = [item["latency_seconds"] for item in items]
            categories[category] = {
                "tests": len(items),
                "pass": sum(item["result"] == "PASS" for item in items),
                "partial": sum(item["result"] == "PARTIAL" for item in items),
                "fail": sum(item["result"] == "FAIL" for item in items),
                "score": round(statistics.fmean(item["score"] for item in items), 2),
                "average_latency_seconds": round(statistics.fmean(latencies), 4),
                "p95_latency_seconds": round(_percentile(latencies, 0.95), 4),
            }
        all_latencies = [item["latency_seconds"] for item in self.results]
        output_characters = sum(len(str(item["actual_answer"])) for item in self.results)
        total_latency = sum(all_latencies)
        gpu_samples = [sample["gpu"] for sample in self.resource_samples]
        ram_samples = [sample["ram"] for sample in self.resource_samples]
        ollama_samples = [
            sample["ollama_model"]
            for sample in self.resource_samples
            if sample["ollama_model"] is not None
        ]
        observed_model_vram = [
            item["size_vram_bytes"]
            for item in ollama_samples
            if item["size_vram_bytes"] > 0
        ]
        return {
            "tests": len(self.results),
            "pass": sum(item["result"] == "PASS" for item in self.results),
            "partial": sum(item["result"] == "PARTIAL" for item in self.results),
            "fail": sum(item["result"] == "FAIL" for item in self.results),
            "score": round(statistics.fmean(item["score"] for item in self.results), 2),
            "categories": categories,
            "latency": {
                "average_seconds": round(statistics.fmean(all_latencies), 4),
                "p95_seconds": round(_percentile(all_latencies, 0.95), 4),
            },
            "throughput": {
                "answer_characters_per_second": round(
                    output_characters / max(total_latency, 0.0001), 2
                ),
                "answer_words_per_second": round(
                    sum(len(str(item["actual_answer"]).split()) for item in self.results)
                    / max(total_latency, 0.0001),
                    2,
                ),
            },
            "stability": {
                "successful_requests": sum(item["request_error"] is None for item in self.results),
                "request_failures": sum(item["request_error"] is not None for item in self.results),
                "thermal_guard_triggered": False,
                "vram_guard_triggered": False,
                "ram_guard_triggered": False,
            },
            "resources": {
                "gpu_name": gpu_samples[0]["name"],
                "gpu_total_mib": gpu_samples[0]["total_mib"],
                "peak_gpu_used_mib": max(item["used_mib"] for item in gpu_samples),
                "minimum_gpu_free_mib": min(item["free_mib"] for item in gpu_samples),
                "peak_gpu_temperature_c": max(
                    item["temperature_c"] for item in gpu_samples
                ),
                "peak_gpu_power_watts": max(item["power_watts"] for item in gpu_samples),
                "ram_total_bytes": ram_samples[0]["total_bytes"],
                "peak_ram_used_bytes": max(item["used_bytes"] for item in ram_samples),
                "minimum_ram_available_bytes": min(
                    item["available_bytes"] for item in ram_samples
                ),
                "peak_model_vram_bytes": (
                    max(observed_model_vram) if observed_model_vram else None
                ),
                "loaded_context_lengths": sorted(
                    {
                        item["context_length"]
                        for item in ollama_samples
                        if item["context_length"] is not None
                    }
                ),
                "ollama_process_visible_samples": len(ollama_samples),
                "ollama_process_sample_count": len(self.resource_samples),
                "ollama_process_telemetry_note": (
                    "The configured zero-second keep-alive unloads the model "
                    "before post-response process sampling; peak GPU memory is "
                    "the authoritative residency bound for this profile."
                    if not ollama_samples
                    else None
                ),
            },
        }

    def write_report(self) -> dict[str, Any]:
        report = {
            "schema_version": 1,
            "created_at_unix": round(time.time(), 3),
            "model_reference": self.model_reference,
            "model_id": self.model_id,
            "model_metadata": self.model_metadata,
            "profile": {
                "id": self.profile,
                "temperature": 0.0,
                "seed": EXPERIMENT_SEED,
                "thinking": (
                    "automatic for the qwen3 exact-output/code-generation profile"
                    if self.profile == QWEN3_THINKING_AUTO_PROFILE
                    else "disabled for code_generation/exact_output; automatic elsewhere"
                ),
                "output_budget": "existing per-case bounded budget",
                "keep_alive": "repository-configured bounded value",
                "production_allowlist_changed": False,
                "production_routing_changed": False,
                "single_text_model_isolation": True,
            },
            "summary": self._summary(),
            "known_failure_results": [
                item
                for item in self.results
                if item["test_id"]
                in {
                    "codegen-typescript-chunks",
                    "codegen-bash-join",
                    "codegen-python-parse-uint",
                    "codegen-rust-dedupe-sorted",
                    "codegen-bash-uint",
                }
            ],
            "results": self.results,
            "duration_seconds": round(time.time() - self.started_at, 3),
        }
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if any(
            token and token in serialized
            for token in (self.owner_token, self.provisioning_token)
        ):
            raise RuntimeError("candidate benchmark report credential scan failed")
        temporary = self.output_path.with_name(f".{self.output_path.name}.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.output_path)
        self.output_path.chmod(0o600)
        return report

    def close(self) -> None:
        self.ollama.close()
        self.client.close()


def _verify_output_path(value: str) -> Path:
    output = Path(value)
    if not output.is_absolute() or output.parent.name != "Work_Station_Benchmark":
        raise RuntimeError(
            "candidate benchmark output must be inside the dedicated report directory"
        )
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.parent.chmod(0o700)
    return output


def aggregate_reports(report_root: Path, inputs: list[Path]) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in inputs]
    for report in reports:
        profile = report.setdefault("profile", {})
        profile.setdefault("id", BASELINE_PROFILE)
        resources = report.get("summary", {}).get("resources", {})
        if resources.get("peak_model_vram_bytes") == 0:
            resources["peak_model_vram_bytes"] = None
            resources.setdefault("ollama_process_visible_samples", 0)
            resources.setdefault("ollama_process_sample_count", None)
            resources.setdefault(
                "ollama_process_telemetry_note",
                "The configured zero-second keep-alive unloads the model before "
                "post-response process sampling; peak GPU memory is the "
                "authoritative residency bound for this profile.",
            )
    baseline_reports = [
        report
        for report in reports
        if report.get("profile", {}).get("id", BASELINE_PROFILE)
        == BASELINE_PROFILE
    ]
    profile_reports = [report for report in reports if report not in baseline_reports]
    references = [report.get("model_reference") for report in baseline_reports]
    if sorted(references) != sorted(MODEL_REFERENCES):
        raise RuntimeError("candidate benchmark aggregation requires all three models")
    if any(
        report.get("profile", {}).get("id") != QWEN3_THINKING_AUTO_PROFILE
        or report.get("model_reference") != "qwen3:8b"
        for report in profile_reports
    ):
        raise RuntimeError("candidate benchmark aggregation received an invalid profile")
    category_names = sorted(
        {
            category
            for report in baseline_reports
            for category in report["summary"]["categories"]
        }
    )
    category_winners = {}
    for category in category_names:
        ranked = sorted(
            (
                {
                    "model_reference": report["model_reference"],
                    **report["summary"]["categories"][category],
                }
                for report in baseline_reports
            ),
            key=lambda item: (
                -item["score"],
                -item["pass"],
                item["fail"],
                item["average_latency_seconds"],
                item["model_reference"],
            ),
        )
        category_winners[category] = {
            "winner": ranked[0]["model_reference"],
            "ranking": ranked,
        }
    aggregate = {
        "schema_version": 1,
        "benchmark_commit": subprocess.run(
            ("git", "rev-parse", "HEAD"),
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip(),
        "production_changed_during_isolated_benchmark": False,
        "models": baseline_reports,
        "profile_experiments": profile_reports,
        "installation_verification": _installed_model_blob_verification(
            "qwen2.5-coder:14b-instruct-q3_K_L"
        ),
        "category_winners": category_winners,
        "routing_decision": _routing_decision(),
    }
    serialized = json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n"
    output = report_root / "model-upgrade-experiment.json"
    temporary = report_root / ".model-upgrade-experiment.json.tmp"
    temporary.write_text(serialized, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(output)
    output.chmod(0o600)
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument("paths", nargs="*")
    arguments = parser.parse_args()
    if arguments.aggregate:
        if len(arguments.paths) not in {4, 5}:
            raise RuntimeError(
                "aggregate mode requires report root, three baselines, and an "
                "optional qwen3 profile report"
            )
        report_root = Path(arguments.paths[0])
        if not report_root.is_absolute() or report_root.name != "Work_Station_Benchmark":
            raise RuntimeError("aggregate report root is invalid")
        aggregate_reports(report_root, [Path(value) for value in arguments.paths[1:]])
        print("MODEL_CANDIDATE_AGGREGATE_COMPLETE")
        return

    api_origin = os.environ.get("WORK_STATION_BENCHMARK_API_ORIGIN", "").strip()
    model_reference = os.environ.get(
        "WORK_STATION_MODEL_EXPERIMENT_REFERENCE", ""
    ).strip()
    profile = os.environ.get(
        "WORK_STATION_MODEL_EXPERIMENT_PROFILE", BASELINE_PROFILE
    ).strip()
    output_path = _verify_output_path(
        os.environ.get("WORK_STATION_MODEL_EXPERIMENT_OUTPUT", "").strip()
    )
    provisioning_token = sys.stdin.read().strip()
    if not api_origin.startswith("http://127.0.0.1:"):
        raise RuntimeError("candidate benchmark requires an isolated loopback API")
    if model_reference not in MODEL_REFERENCES:
        raise RuntimeError("candidate benchmark model reference is not approved")
    if profile not in EXPERIMENT_PROFILES:
        raise RuntimeError("candidate benchmark profile is not approved")
    if (
        profile == QWEN3_THINKING_AUTO_PROFILE
        and model_reference != "qwen3:8b"
    ):
        raise RuntimeError("thinking-auto profile is approved only for qwen3:8b")
    if not provisioning_token:
        raise RuntimeError("candidate benchmark provisioning token is missing")
    runner = CandidateBenchmarkRunner(
        api_origin,
        provisioning_token,
        model_reference,
        output_path,
        profile,
    )
    try:
        runner.initialize()
        runner.run_text_cases()
        runner.run_code_cases()
        report = runner.write_report()
    finally:
        runner.close()
    print("MODEL_CANDIDATE_BENCHMARK_COMPLETE")
    print(f"MODEL_REFERENCE={model_reference}")
    print(f"MODEL_TESTS={report['summary']['tests']}")
    print(f"MODEL_SCORE={report['summary']['score']}")
    print(f"MODEL_OUTPUT={output_path}")


if __name__ == "__main__":
    main()
