#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.ai.routing import ModelTask  # noqa: E402
from app.external_ai import (  # noqa: E402
    EncryptedProviderVault,
    ExternalProviderKind,
    ExternalVerificationEvidence,
)
from app.external_ai.evidence import ExternalEvidenceError, MAX_EVIDENCE_BYTES  # noqa: E402
from app.external_ai.vault import ProviderVaultError  # noqa: E402


def register(state_root: Path, report_path: Path) -> str:
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("evidence report must be a regular file")
    if not 1 <= report_path.stat().st_size <= MAX_EVIDENCE_BYTES:
        raise ValueError("evidence report is outside its size bound")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    expected_keys = {
        "provider_kind",
        "model_id",
        "tasks",
        "benchmark_artifact_sha256",
        "complete_category",
        "passed",
        "measured_quality",
        "measured_latency_ms",
        "stability_rate",
        "context_window",
        "input_cost_micros_per_million_tokens",
        "output_cost_micros_per_million_tokens",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys or not isinstance(payload["tasks"], list):
        raise ValueError("evidence report schema is invalid")
    evidence = ExternalVerificationEvidence(
        provider_kind=ExternalProviderKind(payload["provider_kind"]),
        model_id=payload["model_id"],
        tasks=frozenset(ModelTask(task) for task in payload["tasks"]),
        benchmark_artifact_sha256=payload["benchmark_artifact_sha256"],
        complete_category=payload["complete_category"],
        passed=payload["passed"],
        measured_quality=payload["measured_quality"],
        measured_latency_ms=payload["measured_latency_ms"],
        stability_rate=payload["stability_rate"],
        context_window=payload["context_window"],
        input_cost_micros_per_million_tokens=payload["input_cost_micros_per_million_tokens"],
        output_cost_micros_per_million_tokens=payload["output_cost_micros_per_million_tokens"],
    )
    return EncryptedProviderVault(state_root).register_verification_evidence(evidence)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register complete-category benchmark evidence for one external model.",
    )
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("report", type=Path)
    arguments = parser.parse_args()
    try:
        digest = register(arguments.state_root, arguments.report)
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ExternalEvidenceError,
        ProviderVaultError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
