from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from scripts.model_candidate_benchmark import (
    BASELINE_MODEL_REFERENCES,
    BASELINE_PROFILE,
    CURRENT_HARDWARE_DISCOVERY_REFERENCES,
    _installed_model_blob_verification,
    _read_gpu_snapshot,
)


EXPECTED_CATEGORIES = frozenset(
    {
        "coding",
        "debugging",
        "reasoning",
        "mathematics",
        "expert_analysis",
        "exact_output",
        "long_context",
        "executable_code_generation",
    }
)
MATERIAL_SCORE_DELTA = 2.0
CURRENT_CATEGORY_BASELINES = {
    "coding": "qwen2.5-coder:7b",
    "debugging": "qwen3:8b",
    "reasoning": "qwen3:8b",
    "mathematics": "qwen3:8b",
    "expert_analysis": "qwen3:8b",
    "exact_output": "qwen3:8b",
    "executable_code_generation": "qwen3:8b",
}
OFFICIAL_MANIFEST_RESIDENT_BYTES = {
    "qwen3:14b-q4_K_M": 9_276_184_896,
    "phi4-reasoning:14b-q4_K_M": 11_117_500_000,
    "deepcoder:14b-preview-q4_K_M": 8_988_111_168,
    # Gemma's vision projector is resident model data, so include both layers.
    "gemma4:12b-it-q4_K_M": 7_556_497_632,
    "qwen3.5:9b-q4_K_M": 6_594_462_816,
    "ministral-3:14b-instruct-2512-q4_K_M": 9_082_522_240,
}


def _required_vram_bytes(model_bytes: int) -> int:
    return model_bytes + max(1024**3, model_bytes // 5)


def hardware_admission() -> dict[str, dict[str, Any]]:
    gpu = _read_gpu_snapshot()
    available_vram_bytes = gpu["total_mib"] * 1024**2
    decisions: dict[str, dict[str, Any]] = {}
    for reference, model_bytes in OFFICIAL_MANIFEST_RESIDENT_BYTES.items():
        required = _required_vram_bytes(model_bytes)
        admitted = required <= available_vram_bytes
        decisions[reference] = {
            "official_manifest_resident_bytes": model_bytes,
            "required_vram_bytes_with_repository_reserve": required,
            "detected_gpu_vram_bytes": available_vram_bytes,
            "decision": "admitted_for_isolated_test" if admitted else "rejected_before_download",
            "reason": (
                "model weights plus repository reserve fit detected GPU VRAM"
                if admitted
                else "model weights plus repository reserve exceed detected GPU VRAM"
            ),
        }
    return decisions


def _matrix_fingerprint(report: dict[str, Any]) -> str:
    cases = [
        {
            "test_id": item.get("test_id"),
            "comparison_category": item.get("comparison_category"),
            "prompt": item.get("prompt"),
            "expected_behavior": item.get("expected_behavior"),
        }
        for item in report.get("results", [])
    ]
    payload = json.dumps(cases, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_reports(reports: list[dict[str, Any]]) -> str:
    expected_references = {
        *BASELINE_MODEL_REFERENCES,
        *CURRENT_HARDWARE_DISCOVERY_REFERENCES,
    }
    references = [report.get("model_reference") for report in reports]
    if len(references) != len(set(references)) or set(references) != expected_references:
        raise RuntimeError("hardware discovery aggregation requires every approved model exactly once")
    fingerprints = {_matrix_fingerprint(report) for report in reports}
    if len(fingerprints) != 1:
        raise RuntimeError("hardware discovery reports do not use an identical test matrix")
    for report in reports:
        if report.get("profile", {}).get("id", BASELINE_PROFILE) != BASELINE_PROFILE:
            raise RuntimeError("hardware discovery requires the deterministic baseline profile")
        summary = report.get("summary", {})
        if summary.get("tests") != 221:
            raise RuntimeError("hardware discovery report has an incomplete test matrix")
        if set(summary.get("categories", {})) != EXPECTED_CATEGORIES:
            raise RuntimeError("hardware discovery report has incomplete categories")
        if report.get("profile", {}).get("production_routing_changed") is not False:
            raise RuntimeError("hardware discovery was not production-isolated")
    return next(iter(fingerprints))


def _rank_categories(reports: list[dict[str, Any]]) -> dict[str, Any]:
    ranked_categories: dict[str, Any] = {}
    by_reference = {report["model_reference"]: report for report in reports}
    for category in sorted(EXPECTED_CATEGORIES):
        ranking = sorted(
            (
                {
                    "model_reference": report["model_reference"],
                    **report["summary"]["categories"][category],
                }
                for report in reports
            ),
            key=lambda item: (
                -item["score"],
                -item["pass"],
                item["fail"],
                item["average_latency_seconds"],
                item["model_reference"],
            ),
        )
        current_reference = CURRENT_CATEGORY_BASELINES.get(category)
        current = (
            by_reference[current_reference]["summary"]["categories"][category]
            if current_reference is not None
            else None
        )
        winner = ranking[0]
        winner_report = by_reference[winner["model_reference"]]
        stability = winner_report["summary"]["stability"]
        candidate_winner = winner["model_reference"] in CURRENT_HARDWARE_DISCOVERY_REFERENCES
        material = bool(
            candidate_winner
            and current is not None
            and winner["score"] >= current["score"] + MATERIAL_SCORE_DELTA
            and winner["pass"] >= current["pass"]
            and winner["fail"] <= current["fail"]
            and stability["request_failures"] == 0
            and not stability["thermal_guard_triggered"]
            and not stability["vram_guard_triggered"]
            and not stability["ram_guard_triggered"]
        )
        ranked_categories[category] = {
            "winner": winner["model_reference"],
            "current_production_reference": current_reference,
            "score_delta_from_current": (
                round(winner["score"] - current["score"], 2)
                if current is not None
                else None
            ),
            "material_route_improvement": material,
            "route_recommendation": (
                winner["model_reference"] if material else current_reference
            ),
            "ranking": ranking,
        }
    return ranked_categories


def aggregate_discovery_reports(report_root: Path, inputs: list[Path]) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in inputs]
    matrix_fingerprint = _validate_reports(reports)
    admission = hardware_admission()
    if admission["phi4-reasoning:14b-q4_K_M"]["decision"] != "rejected_before_download":
        raise RuntimeError("Phi-4 admission expectation no longer matches detected hardware")
    ranked_categories = _rank_categories(reports)
    installations = {
        reference: _installed_model_blob_verification(reference)
        for reference in CURRENT_HARDWARE_DISCOVERY_REFERENCES
    }
    route_changes = {
        category: result["route_recommendation"]
        for category, result in ranked_categories.items()
        if result["material_route_improvement"]
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
        "deterministic_profile": {
            "temperature": 0.0,
            "seed": 20260827,
            "bounded_output_budget": True,
            "single_text_model_isolation": True,
            "original_answers_scored_before_execution": True,
            "examiner_repaired_artifacts": False,
        },
        "matrix_sha256": matrix_fingerprint,
        "material_score_delta": MATERIAL_SCORE_DELTA,
        "hardware_admission": admission,
        "installation_verification": installations,
        "models": reports,
        "category_results": ranked_categories,
        "routing_recommendation": {
            "changes": route_changes,
            "requires_production_change": bool(route_changes),
            "long_context_note": (
                "Long-context discovery is ranked but cannot change production routing "
                "without a direct complete-category comparison against the current "
                "vision/long-context route."
            ),
        },
    }
    serialized = json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n"
    output = report_root / "current-hardware-model-discovery.json"
    temporary = report_root / ".current-hardware-model-discovery.json.tmp"
    temporary.write_text(serialized, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(output)
    output.chmod(0o600)
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_root")
    parser.add_argument("reports", nargs="+")
    arguments = parser.parse_args()
    report_root = Path(arguments.report_root)
    if not report_root.is_absolute() or report_root.name != "Work_Station_Benchmark":
        raise RuntimeError("hardware discovery report root is invalid")
    aggregate_discovery_reports(report_root, [Path(value) for value in arguments.reports])
    print("CURRENT_HARDWARE_MODEL_DISCOVERY_AGGREGATE_COMPLETE")


if __name__ == "__main__":
    main()
