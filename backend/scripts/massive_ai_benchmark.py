from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

import httpx


MAX_INTERACTIONS = 100_000_000
MAX_FAILURE_SAMPLES = 100
MAX_LATENCY_SAMPLES = 10_000
MIN_AVAILABLE_MEMORY_MIB = 2_048
CRITICAL_AVAILABLE_MEMORY_MIB = 1_024
MAX_GPU_TEMPERATURE_C = 85
CRITICAL_GPU_TEMPERATURE_C = 90


@dataclass(frozen=True, slots=True)
class SyntheticInteraction:
    index: int
    kind: str
    method: str
    path: str
    body: dict[str, Any] | None
    authenticated: bool
    expected_status: int
    expected_value: Any = None


@dataclass(slots=True)
class Aggregate:
    seed: int
    total_target: int
    completed: int = 0
    passed: int = 0
    failed: int = 0
    latency_sum: float = 0.0
    max_latency: float = 0.0
    latency_samples: list[float] = field(default_factory=list)
    kinds: Counter[str] = field(default_factory=Counter)
    failure_samples: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        interaction: SyntheticInteraction,
        *,
        passed: bool,
        latency: float,
        reason: str | None,
    ) -> None:
        self.completed += 1
        self.passed += int(passed)
        self.failed += int(not passed)
        self.latency_sum += latency
        self.max_latency = max(self.max_latency, latency)
        self.kinds[interaction.kind] += 1
        if len(self.latency_samples) < MAX_LATENCY_SAMPLES:
            self.latency_samples.append(latency)
        else:
            selection = random.Random(self.seed ^ interaction.index).randrange(
                self.completed
            )
            if selection < MAX_LATENCY_SAMPLES:
                self.latency_samples[selection] = latency
        if not passed and len(self.failure_samples) < MAX_FAILURE_SAMPLES:
            self.failure_samples.append(
                {
                    "case_id": f"massive-{interaction.index:08d}",
                    "kind": interaction.kind,
                    "failure_reason": reason or "objective response mismatch",
                }
            )

    def document(self) -> dict[str, Any]:
        ordered = sorted(self.latency_samples)
        p95 = (
            ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]
            if ordered
            else 0.0
        )
        return {
            "seed": self.seed,
            "target_interactions": self.total_target,
            "completed_interactions": self.completed,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(100 * self.passed / max(1, self.completed), 4),
            "average_latency_seconds": round(
                self.latency_sum / max(1, self.completed), 6
            ),
            "p95_latency_seconds": round(p95, 6),
            "max_latency_seconds": round(self.max_latency, 6),
            "latency_sample_size": len(self.latency_samples),
            "interaction_kinds": dict(sorted(self.kinds.items())),
            "failure_samples": self.failure_samples,
        }


def _tier(interactions: int) -> str:
    if interactions >= 100_000_000:
        return "E"
    if interactions >= 10_000_000:
        return "D"
    if interactions >= 1_000_000:
        return "C"
    if interactions >= 100_000:
        return "B"
    return "A"


def _interaction(seed: int, index: int) -> SyntheticInteraction:
    selector = index % 20
    # Exercise fail-closed authentication without intentionally tripping the
    # product's peer-level brute-force circuit breaker during a healthy load
    # run. Ten thousand interactions therefore include ten real 401 checks.
    if index % 1_000 == 0:
        return SyntheticInteraction(
            index,
            "unauthorized_access",
            "GET",
            "/api/v1/tools",
            None,
            False,
            401,
        )
    if selector == 1:
        return SyntheticInteraction(
            index,
            "unsafe_expression_rejection",
            "POST",
            "/api/v1/tools/calculator/executions",
            {"arguments": {"expression": "__import__('os').system('id')"}},
            True,
            201,
            {"status": "failed", "result": None},
        )
    if selector == 2:
        return SyntheticInteraction(
            index,
            "tool_registry",
            "GET",
            "/api/v1/tools",
            None,
            True,
            200,
            {"tool_count": 5},
        )
    if selector == 3:
        marker = f"SYNTHETIC-{seed:08x}-{index:08d}"
        return SyntheticInteraction(
            index,
            "conversation_turn",
            "POST",
            "/api/v1/conversations",
            {"initial_message": marker, "title": "Disposable massive benchmark"},
            True,
            201,
            {"initial_message": marker},
        )

    generator = random.Random(seed ^ (index * 0x9E3779B1))
    left = generator.randint(-500, 500)
    right = generator.randint(-500, 500)
    factor = generator.randint(1, 19)
    expression = f"({left})+({right})*{factor}"
    return SyntheticInteraction(
        index,
        "calculator",
        "POST",
        "/api/v1/tools/calculator/executions",
        {"arguments": {"expression": expression}},
        True,
        201,
        {"value": left + right * factor},
    )


def _validate_response(
    interaction: SyntheticInteraction,
    response: httpx.Response,
) -> tuple[bool, str | None]:
    if response.status_code != interaction.expected_status:
        return False, f"http_status:{response.status_code}"
    if interaction.expected_value is None:
        return True, None
    try:
        body = response.json()
    except (ValueError, TypeError):
        return False, "invalid_json"
    if interaction.kind == "calculator":
        passed = body.get("status") == "completed" and body.get("result") == interaction.expected_value
    elif interaction.kind == "unsafe_expression_rejection":
        passed = all(body.get(key) == value for key, value in interaction.expected_value.items())
    elif interaction.kind == "tool_registry":
        passed = len(body.get("items", [])) == interaction.expected_value["tool_count"]
    elif interaction.kind == "conversation_turn":
        initial_message = body.get("initial_message", {})
        passed = (
            isinstance(initial_message, dict)
            and initial_message.get("sequence_number") == 1
            and initial_message.get("content")
            == interaction.expected_value["initial_message"]
        )
    else:  # pragma: no cover - every generated kind is handled above
        passed = False
    return passed, None if passed else "objective_response_mismatch"


def _available_memory_mib() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _gpu_state() -> tuple[int | None, int | None, int | None]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        values = [int(value.strip()) for value in completed.stdout.splitlines()[0].split(",")]
        return values[0], values[1], values[2]
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None, None, None


def _safe_worker_count(requested: int) -> tuple[int, dict[str, Any]]:
    available = _available_memory_mib()
    temperature, gpu_used, gpu_total = _gpu_state()
    if available and available < CRITICAL_AVAILABLE_MEMORY_MIB:
        return 0, {
            "reason": "critical_memory_pressure",
            "available_memory_mib": available,
        }
    workers = requested
    reasons: list[str] = []
    if available and available < MIN_AVAILABLE_MEMORY_MIB:
        workers = 1
        reasons.append("memory_pressure")
    if temperature is not None and temperature >= CRITICAL_GPU_TEMPERATURE_C:
        return 0, {"reason": "critical_gpu_temperature", "gpu_temperature_c": temperature}
    if temperature is not None and temperature >= MAX_GPU_TEMPERATURE_C:
        workers = 1
        reasons.append("gpu_temperature")
    return workers, {
        "reasons": reasons,
        "available_memory_mib": available,
        "gpu_temperature_c": temperature,
        "gpu_memory_used_mib": gpu_used,
        "gpu_memory_total_mib": gpu_total,
    }


async def _execute(
    client: httpx.AsyncClient,
    interaction: SyntheticInteraction,
    owner_headers: dict[str, str],
    semaphore: asyncio.Semaphore,
) -> tuple[SyntheticInteraction, bool, float, str | None]:
    headers = owner_headers if interaction.authenticated else {}
    started = time.perf_counter()
    try:
        async with semaphore:
            response = await client.request(
                interaction.method,
                interaction.path,
                headers=headers,
                json=interaction.body,
            )
        passed, reason = _validate_response(interaction, response)
    except httpx.HTTPError:
        passed, reason = False, "request_failed"
    return interaction, passed, time.perf_counter() - started, reason


def _atomic_report(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _publish_summary(path: Path, summary: dict[str, Any]) -> bool:
    """Publish a completed run without replacing a larger valid result."""
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
            raise OSError("existing report is not a bounded regular file")
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        existing = None
    if (
        isinstance(existing, dict)
        and existing.get("status") == "COMPLETE"
        and existing.get("disposable_database") is True
        and existing.get("production_data_modified") is False
        and existing.get("failed") == 0
        and existing.get("maximum_supported_interactions")
        == summary.get("maximum_supported_interactions")
        and isinstance(existing.get("completed_interactions"), int)
        and not isinstance(existing.get("completed_interactions"), bool)
        and existing["completed_interactions"]
        > summary.get("completed_interactions", 0)
    ):
        return False
    _atomic_report(path, summary)
    return True


async def _run(
    *,
    api_origin: str,
    provisioning_token: str,
    report_root: Path,
    interactions: int,
    requested_workers: int,
    batch_size: int,
    seed: int,
) -> dict[str, Any]:
    checkpoint_path = report_root / ".massive-benchmark-checkpoint.json"
    aggregate = Aggregate(seed=seed, total_target=interactions)
    next_index = 0
    checkpoint = _read_checkpoint(checkpoint_path, interactions, seed)
    if checkpoint is not None:
        next_index = checkpoint["next_index"]
        aggregate = checkpoint["aggregate"]

    started = time.time()
    resource_samples: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=api_origin,
        timeout=httpx.Timeout(30.0, connect=5.0),
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_connections=max(2, requested_workers * 2)),
    ) as client:
        provision = await client.post(
            "/api/v1/users",
            headers={"X-User-Provisioning-Token": provisioning_token},
            json={},
        )
        provision.raise_for_status()
        owner_token = provision.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        while next_index < interactions:
            ready = await client.get("/api/v1/health/ready")
            if ready.status_code != 200:
                raise RuntimeError("isolated backend became unavailable")
            workers, resource = _safe_worker_count(requested_workers)
            resource["at_interaction"] = next_index
            resource["workers"] = workers
            resource_samples.append(resource)
            if workers < 1:
                raise RuntimeError(str(resource.get("reason", "resource pressure")))
            end = min(next_index + batch_size, interactions)
            semaphore = asyncio.Semaphore(workers)
            outcomes = await asyncio.gather(
                *(
                    _execute(
                        client,
                        _interaction(seed, index),
                        owner_headers,
                        semaphore,
                    )
                    for index in range(next_index, end)
                )
            )
            for interaction, passed, latency, reason in outcomes:
                aggregate.record(
                    interaction,
                    passed=passed,
                    latency=latency,
                    reason=reason,
                )
            next_index = end
            _write_checkpoint(checkpoint_path, next_index, aggregate)
            if aggregate.completed >= 100 and aggregate.failed / aggregate.completed > 0.05:
                raise RuntimeError("massive benchmark failure rate exceeded safety bound")
            if next_index % 1_000 == 0 or next_index == interactions:
                print(f"MASSIVE_BENCHMARK_PROGRESS={next_index}/{interactions}", flush=True)

    owner_token = ""
    if checkpoint_path.is_file():
        checkpoint_path.unlink()
    summary = {
        "status": "COMPLETE" if aggregate.completed == interactions else "INCOMPLETE",
        "tier_executed": _tier(interactions),
        "maximum_supported_interactions": MAX_INTERACTIONS,
        "largest_safe_tier_executed": _tier(interactions),
        "disposable_database": True,
        "production_data_modified": False,
        "bounded_parallel_workers_requested": requested_workers,
        "batch_size": batch_size,
        "duration_seconds": round(time.time() - started, 3),
        "resource_samples": resource_samples[-100:],
        "reproduction": (
            "./scripts/postgres_integration_check.sh --massive-benchmark "
            f"(WORK_STATION_MASSIVE_INTERACTIONS={interactions})"
        ),
        **aggregate.document(),
    }
    _publish_summary(report_root / "massive-run-summary.json", summary)
    return summary


def _write_checkpoint(path: Path, next_index: int, aggregate: Aggregate) -> None:
    aggregate_document = aggregate.document()
    aggregate_document["latency_sum"] = aggregate.latency_sum
    aggregate_document["latency_samples"] = aggregate.latency_samples
    document = {
        "next_index": next_index,
        "aggregate": aggregate_document,
    }
    _atomic_report(path, document)


def _read_checkpoint(
    path: Path,
    interactions: int,
    seed: int,
) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
        stored = document["aggregate"]
        if stored["target_interactions"] != interactions or stored["seed"] != seed:
            return None
        if stored["failed"]:
            return None
        aggregate = Aggregate(
            seed=seed,
            total_target=interactions,
            completed=stored["completed_interactions"],
            passed=stored["passed"],
            failed=stored["failed"],
            latency_sum=stored["latency_sum"],
            max_latency=stored["max_latency_seconds"],
            latency_samples=list(stored.get("latency_samples", [])),
            kinds=Counter(stored["interaction_kinds"]),
            failure_samples=list(stored["failure_samples"]),
        )
        return {"next_index": document["next_index"], "aggregate": aggregate}
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--interactions",
        type=int,
        default=int(os.environ.get("WORK_STATION_MASSIVE_INTERACTIONS", "10000")),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("WORK_STATION_MASSIVE_WORKERS", "4")),
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260825)
    args = parser.parse_args()
    if not 1 <= args.interactions <= MAX_INTERACTIONS:
        raise RuntimeError(f"interactions must be between 1 and {MAX_INTERACTIONS}")
    if not 1 <= args.workers <= 16:
        raise RuntimeError("workers must be between 1 and 16")
    if not 1 <= args.batch_size <= 1_000:
        raise RuntimeError("batch size must be between 1 and 1000")

    api_origin = os.environ.get("WORK_STATION_BENCHMARK_API_ORIGIN", "").strip()
    report_value = os.environ.get("WORK_STATION_BENCHMARK_REPORT_ROOT", "").strip()
    provisioning_token = sys.stdin.read().strip()
    if not api_origin.startswith("http://127.0.0.1:"):
        raise RuntimeError("massive benchmark requires isolated IPv4 loopback API")
    report_root = Path(report_value)
    if not report_root.is_absolute() or report_root.name != "Work_Station_Benchmark":
        raise RuntimeError("massive benchmark report root is invalid")
    if not provisioning_token:
        raise RuntimeError("provisioning credential was not piped in memory")
    report_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_root.chmod(0o700)
    summary = asyncio.run(
        _run(
            api_origin=api_origin,
            provisioning_token=provisioning_token,
            report_root=report_root,
            interactions=args.interactions,
            requested_workers=args.workers,
            batch_size=args.batch_size,
            seed=args.seed,
        )
    )
    provisioning_token = ""
    print("MASSIVE_BENCHMARK_COMPLETE")
    print(f"MASSIVE_BENCHMARK_INTERACTIONS={summary['completed_interactions']}")
    print(f"MASSIVE_BENCHMARK_PASS_RATE={summary['pass_rate']}")


if __name__ == "__main__":
    main()
