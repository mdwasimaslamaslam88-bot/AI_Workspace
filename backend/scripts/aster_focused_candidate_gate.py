#!/usr/bin/env python3
"""Trusted parent-side focused gate for one locally admitted Ollama candidate.

The ASTER controller invokes this runner from the host process through the
existing disposable PostgreSQL/backend integration boundary.  Codex children
may inspect and reason about the resulting evidence, but they are not trusted
with runtime access or objective-result creation.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
from typing import Any
import uuid

import httpx

from app.ai.catalog import public_model_id
from scripts.ai_benchmark_cases import BenchmarkCase, build_text_matrix
from scripts.ai_quality_benchmark import _evaluate_answer
from scripts.model_candidate_benchmark import (
    _installed_model_blob_verification,
    _read_gpu_snapshot,
    _read_memory_snapshot,
)


ROUTES = (
    "explicit_candidate",
    "generic_coding",
    "generic_code_generation",
    "automatic_task_aware_agent_os",
)
SEED = 20260827
BASE_SYSTEM_PROMPT = (
    "You are WORK STATION under an objective benchmark. Follow the user's "
    "explicit output contract. Do not reveal hidden reasoning, credentials, "
    "private paths, or unrelated content."
)
TERMINAL_AGENT_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
MODEL_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+:[A-Za-z0-9._-]+$")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = value if isinstance(value, str) else _safe_json(value)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(serialized)
        handle.flush()
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _cases() -> tuple[BenchmarkCase, BenchmarkCase]:
    source = {case.test_id: case for case in build_text_matrix()}
    coding = source.get("medium-coding-04")
    if coding is None:
        raise RuntimeError("unchanged medium-coding-04 benchmark case is unavailable")
    recursion = BenchmarkCase(
        test_id="model-comparison-coder-06",
        category="model_comparison",
        difficulty="expert",
        prompt=(
            "A recursive function has no terminating condition. What is the "
            "standard recursion term for the missing condition? Name it only."
        ),
        expected_behavior="Solve the same objective task for cross-model comparison.",
        required=("base case",),
        max_output_tokens=100,
    )
    if coding.prompt != (
        "Write a Python list comprehension producing squares of 0 through 4. "
        "Expression only."
    ) or coding.required != ("[", "x * x", "range(5)"):
        raise RuntimeError("unchanged medium-coding-04 contract has changed")
    return coding, recursion


def _independent_semantic_check(case: BenchmarkCase, raw: str) -> dict[str, Any]:
    if case.test_id == "model-comparison-coder-06":
        passed = raw.strip().casefold() == "base case"
        return {
            "check": "exact_name_only_base_case",
            "passed": passed,
            "normalized_for_check": raw.strip(),
        }

    source = raw.strip()
    evidence: dict[str, Any] = {
        "check": "exact_list_comprehension_x_squared_range_5",
        "contains_x_times_x": "x * x" in raw,
        "contains_range_5": "range(5)" in raw,
        "expression_only": not source.startswith("```") and "\n" not in source,
    }
    try:
        tree = ast.parse(source, mode="eval")
        body = tree.body
        generator = body.generators[0] if isinstance(body, ast.ListComp) and len(body.generators) == 1 else None
        target = generator.target if generator is not None else None
        iterator = generator.iter if generator is not None else None
        element = body.elt if isinstance(body, ast.ListComp) else None
        multiplication = (
            isinstance(element, ast.BinOp)
            and isinstance(element.op, ast.Mult)
            and isinstance(element.left, ast.Name)
            and element.left.id == "x"
            and isinstance(element.right, ast.Name)
            and element.right.id == "x"
        )
        range_call = (
            isinstance(iterator, ast.Call)
            and isinstance(iterator.func, ast.Name)
            and iterator.func.id == "range"
            and len(iterator.args) == 1
            and isinstance(iterator.args[0], ast.Constant)
            and iterator.args[0].value == 5
            and not iterator.keywords
        )
        target_x = isinstance(target, ast.Name) and target.id == "x"
        evidence.update(
            {
                "syntax_valid": True,
                "list_comprehension": isinstance(body, ast.ListComp),
                "target_x": target_x,
                "multiplication_x_x": multiplication,
                "range_5_call": range_call,
            }
        )
        evidence["passed"] = bool(
            evidence["contains_x_times_x"]
            and evidence["contains_range_5"]
            and evidence["expression_only"]
            and evidence["list_comprehension"]
            and target_x
            and multiplication
            and range_call
        )
    except (SyntaxError, ValueError, TypeError):
        evidence.update({"syntax_valid": False, "passed": False})
    return evidence


class FocusedGate:
    def __init__(self, origin: str, provisioning_token: str, candidate: str, output: Path) -> None:
        if not MODEL_REFERENCE_PATTERN.fullmatch(candidate):
            raise ValueError("candidate model reference is invalid")
        self.origin = origin.rstrip("/")
        self.provisioning_token = provisioning_token
        self.candidate = candidate
        self.candidate_model_id = public_model_id("ollama-local", candidate)
        self.output = output
        self.raw_root = output.parent / "raw"
        self.client = httpx.Client(
            base_url=self.origin,
            timeout=httpx.Timeout(240.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
        )
        self.ollama = httpx.Client(
            base_url="http://127.0.0.1:11434",
            timeout=httpx.Timeout(15.0, connect=2.0),
            follow_redirects=False,
            trust_env=False,
        )
        self.owner_token = ""
        self.owner_id = ""
        self.model_metadata: dict[str, Any] = {}
        self.records: list[dict[str, Any]] = []
        self.started_at = time.time()

    def _resource_sample(self) -> dict[str, Any]:
        sample: dict[str, Any] = {"captured_at_unix": round(time.time(), 3)}
        try:
            sample["gpu"] = _read_gpu_snapshot()
        except (OSError, RuntimeError, ValueError) as error:
            sample["gpu_error"] = type(error).__name__
        try:
            sample["ram"] = _read_memory_snapshot()
        except (OSError, RuntimeError, ValueError) as error:
            sample["ram_error"] = type(error).__name__
        try:
            response = self.ollama.get("/api/ps")
            response.raise_for_status()
            models = response.json().get("models", [])
            sample["ollama_models"] = [
                {
                    key: item.get(key)
                    for key in ("model", "name", "size", "size_vram", "context_length")
                }
                for item in models
                if isinstance(item, dict)
            ]
        except (httpx.HTTPError, ValueError, TypeError):
            sample["ollama_ps_error"] = True
        return sample

    def initialize(self) -> dict[str, Any]:
        live = self.client.get("/api/v1/health/live")
        live.raise_for_status()
        provision = self.client.post(
            "/api/v1/users",
            headers={"X-User-Provisioning-Token": self.provisioning_token},
            json={},
        )
        provision.raise_for_status()
        provisioned = provision.json()
        self.owner_token = str(provisioned["access_token"])
        self.owner_id = str(provisioned["id"])
        current = self.client.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {self.owner_token}"}
        )
        current.raise_for_status()
        current_user = current.json()
        if current_user.get("id") != self.owner_id:
            raise RuntimeError("provisioned owner identity did not round-trip")
        models_response = self.client.get(
            "/api/v1/ai/models", headers={"Authorization": f"Bearer {self.owner_token}"}
        )
        models_response.raise_for_status()
        models = models_response.json().get("items", [])
        matching = [item for item in models if item.get("model_id") == self.candidate_model_id]
        if len(matching) != 1:
            raise RuntimeError("candidate is absent from the isolated current catalog")
        self.model_metadata = matching[0]
        required = ("installed", "verified", "runnable_now")
        if not all(self.model_metadata.get(key) is True for key in required):
            raise RuntimeError("candidate failed isolated catalog/hardware admission")
        if "text_generation" not in self.model_metadata.get("capabilities", []):
            raise RuntimeError("candidate lacks text-generation capability")
        blob = _installed_model_blob_verification(self.candidate)
        return {
            "authenticated_owner": {
                "provisioned_owner_id": self.owner_id,
                "current_user_id_matches": True,
                "token_persisted": False,
            },
            "candidate_model_id": self.candidate_model_id,
            "catalog_record": self.model_metadata,
            "installed_manifest_blob": blob,
            "initial_resources": self._resource_sample(),
            "transport": {
                "health_status": live.status_code,
                "provision_status": provision.status_code,
                "current_user_status": current.status_code,
                "models_status": models_response.status_code,
            },
        }

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.owner_token}"}

    def _conversation(self, route: str, case: BenchmarkCase) -> tuple[str, dict[str, Any]]:
        create = self.client.post(
            "/api/v1/conversations",
            headers=self.headers,
            json={
                "title": f"ASTER focused {case.test_id}",
                "system_prompt": BASE_SYSTEM_PROMPT,
                "initial_message": case.prompt,
            },
        )
        create.raise_for_status()
        conversation_id = create.json()["id"]
        selection: dict[str, str]
        if route == "explicit_candidate":
            selection = {"model_id": self.candidate_model_id}
        elif route == "generic_coding":
            selection = {"task": "coding"}
        else:
            selection = {"task": "code_generation"}
        started = time.perf_counter()
        generated = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages/generate",
            headers=self.headers,
            json={
                **selection,
                "max_output_tokens": case.max_output_tokens,
                "temperature": 0.0,
                "seed": SEED,
            },
        )
        latency = time.perf_counter() - started
        generated.raise_for_status()
        payload = generated.json()
        message = payload.get("message", {})
        answer = message.get("content")
        if not isinstance(answer, str):
            raise RuntimeError("conversation generation returned no text content")
        return answer, {
            "conversation_create_http_status": create.status_code,
            "generation_http_status": generated.status_code,
            "conversation_id": conversation_id,
            "selection": selection,
            "selected_model_id": payload.get("model_id"),
            "latency_seconds": round(latency, 4),
            "configuration": {
                "system_prompt": BASE_SYSTEM_PROMPT,
                "max_output_tokens": case.max_output_tokens,
                "temperature": 0.0,
                "seed": SEED,
            },
            "resource_sample": self._resource_sample(),
        }

    def _agent_os(self, case: BenchmarkCase) -> tuple[str, dict[str, Any]]:
        task = "coding" if case.test_id == "medium-coding-04" else "debugging"
        started = time.perf_counter()
        created = self.client.post(
            "/api/v1/agent-os/runs",
            headers=self.headers,
            json={
                "goal": case.prompt,
                "task": task,
                "source": "text",
                "max_retries": 0,
                "deadline_seconds": 180,
                "required_context_tokens": 0,
                "require_objective_evidence": False,
                "require_owner_approval": False,
            },
        )
        created.raise_for_status()
        initial = created.json()
        run_id = initial.get("id")
        if not isinstance(run_id, str):
            raise RuntimeError("Agent OS returned no run ID")
        current = initial
        deadline = time.monotonic() + 210
        statuses = [initial.get("status")]
        while current.get("status") not in TERMINAL_AGENT_STATUSES and time.monotonic() < deadline:
            time.sleep(0.1)
            fetched = self.client.get(f"/api/v1/agent-os/runs/{run_id}", headers=self.headers)
            fetched.raise_for_status()
            current = fetched.json()
            statuses.append(current.get("status"))
        if current.get("status") not in TERMINAL_AGENT_STATUSES:
            raise RuntimeError("Agent OS focused run exceeded bounded deadline")
        answer = current.get("output")
        if not isinstance(answer, str):
            answer = ""
        attempts = current.get("attempts", [])
        selected_model_id = attempts[-1].get("model_id") if attempts and isinstance(attempts[-1], dict) else None
        if current.get("status") != "completed":
            raise RuntimeError(f"Agent OS focused run ended {current.get('status')}")
        return answer, {
            "create_http_status": created.status_code,
            "poll_statuses": statuses,
            "endpoint": "/api/v1/agent-os/runs",
            "run_id": run_id,
            "agent_status": current.get("status"),
            "selected_model_id": selected_model_id,
            "agent_plan": current.get("plan", []),
            "agent_attempts": attempts,
            "agent_events": current.get("events", []),
            "latency_seconds": round(time.perf_counter() - started, 4),
            "configuration": {
                "automatic_local_selection": True,
                "task": task,
                "goal_is_unchanged_case_prompt": True,
                "max_retries": 0,
                "deadline_seconds": 180,
                "require_objective_evidence": False,
            },
            "resource_sample": self._resource_sample(),
        }

    def _record(self, route: str, case: BenchmarkCase) -> dict[str, Any]:
        answer = ""
        transport: dict[str, Any] = {}
        request_error: str | None = None
        try:
            if route == "automatic_task_aware_agent_os":
                answer, transport = self._agent_os(case)
            else:
                answer, transport = self._conversation(route, case)
        except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as error:
            request_error = f"{type(error).__name__}: {error}"
        raw = answer.encode("utf-8")
        raw_path = self.raw_root / f"{route}__{case.test_id}.txt"
        _write_atomic(raw_path, answer)
        latency = float(transport.get("latency_seconds", 0.0) or 0.0)
        canonical = _evaluate_answer(case, answer, latency)
        independent = _independent_semantic_check(case, answer)
        selected_model_id = transport.get("selected_model_id")
        objective_pass = bool(
            request_error is None
            and selected_model_id == self.candidate_model_id
            and canonical.get("result") == "PASS"
            and independent.get("passed") is True
        )
        record = {
            "route": route,
            "test_id": case.test_id,
            "prompt": case.prompt,
            "expected_behavior": case.expected_behavior,
            "canonical_case": {
                "category": case.category,
                "difficulty": case.difficulty,
                "required": list(case.required),
                "max_output_tokens": case.max_output_tokens,
            },
            "selected_model_id": selected_model_id,
            "candidate_model_id": self.candidate_model_id,
            "raw_output": answer,
            "raw_output_encoding": "utf-8",
            "raw_output_bytes": len(raw),
            "raw_output_sha256": _sha256_bytes(raw),
            "raw_output_path": str(raw_path),
            "latency_seconds": latency,
            "request_configuration": transport.get("configuration", {}),
            "transport": {key: value for key, value in transport.items() if key != "configuration"},
            "canonical_evaluation": canonical,
            "existing_independent_semantic_verification": independent,
            "objective_pass": objective_pass,
            "request_error": request_error,
            "source_commit": os.environ.get("ASTER_APPLICATION_SOURCE_COMMIT", "unknown"),
        }
        return record

    def run(self) -> dict[str, Any]:
        candidate_metadata: dict[str, Any] = {}
        initialization_error: str | None = None
        try:
            candidate_metadata = self.initialize()
        except (httpx.HTTPError, KeyError, RuntimeError, ValueError, OSError) as error:
            initialization_error = f"{type(error).__name__}: {error}"
        cases = _cases()
        if initialization_error is None:
            for route in ROUTES:
                for case in cases:
                    self.records.append(self._record(route, case))
        else:
            for route in ROUTES:
                for case in cases:
                    self.records.append(
                        {
                            "route": route,
                            "test_id": case.test_id,
                            "prompt": case.prompt,
                            "selected_model_id": None,
                            "candidate_model_id": self.candidate_model_id,
                            "raw_output": "",
                            "raw_output_bytes": 0,
                            "raw_output_sha256": _sha256_bytes(b""),
                            "objective_pass": False,
                            "request_error": initialization_error,
                            "source_commit": os.environ.get("ASTER_APPLICATION_SOURCE_COMMIT", "unknown"),
                        }
                    )
        objective_pass_count = sum(record.get("objective_pass") is True for record in self.records)
        report = {
            "schema_version": 1,
            "run_status": "complete" if initialization_error is None else "blocked",
            "created_at_unix": round(self.started_at, 3),
            "finished_at_unix": round(time.time(), 3),
            "candidate_reference": self.candidate,
            "candidate_model_id": self.candidate_model_id,
            "source_commit": os.environ.get("ASTER_APPLICATION_SOURCE_COMMIT", "unknown"),
            "candidate_metadata": candidate_metadata,
            "initialization_error": initialization_error,
            "route_count": len(ROUTES),
            "case_count": len(cases),
            "matrix_complete": len(self.records) == 8,
            "objective_pass_count": objective_pass_count,
            "all_eight_objective_pass": objective_pass_count == 8,
            "production_routing_modified": False,
            "records": self.records,
        }
        _write_atomic(self.output, report)
        return report

    def close(self) -> None:
        self.client.close()
        self.ollama.close()


def main() -> int:
    candidate = os.environ.get("ASTER_FOCUSED_CANDIDATE", "").strip()
    output_value = os.environ.get("ASTER_FOCUSED_OUTPUT", "").strip()
    api_origin = os.environ.get("WORK_STATION_BENCHMARK_API_ORIGIN", "").strip()
    token = sys.stdin.read().strip()
    if not candidate or not output_value or not api_origin.startswith("http://127.0.0.1:") or not token:
        print("ASTER_FOCUSED_GATE_INVALID_CONFIGURATION", file=sys.stderr)
        return 2
    output = Path(output_value)
    if not output.is_absolute() or output.name != "focused-candidate-gate.json":
        print("ASTER_FOCUSED_GATE_INVALID_OUTPUT", file=sys.stderr)
        return 2
    runner = FocusedGate(api_origin, token, candidate, output)
    try:
        report = runner.run()
        print("ASTER_FOCUSED_GATE_COMPLETE")
        print(f"ASTER_FOCUSED_GATE_OBJECTIVE_PASS={report['objective_pass_count']}/8")
        print(f"ASTER_FOCUSED_GATE_OUTPUT={output}")
        return 0 if report["run_status"] == "complete" else 86
    finally:
        runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
