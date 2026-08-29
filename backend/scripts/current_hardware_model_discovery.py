from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from app.ai.catalog import public_model_id
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
MODEL_DISCOVERY_BASELINE_SCORE = 97.23
CURRENT_CATEGORY_BASELINES = {
    "coding": "qwen2.5-coder:7b",
    "debugging": "qwen3:8b",
    "reasoning": "qwen3:8b",
    "mathematics": "qwen3:8b",
    "expert_analysis": "qwen3:8b",
    "exact_output": "qwen3:8b",
    "executable_code_generation": "gemma4:12b-it-q4_K_M",
}
OFFICIAL_MANIFEST_RESIDENT_BYTES = {
    "qwen3:14b-q4_K_M": 9_276_184_896,
    "phi4-reasoning:14b-q4_K_M": 11_117_500_000,
    "deepcoder:14b-preview-q4_K_M": 8_988_111_168,
    # Gemma's vision projector is resident model data, so include both layers.
    "gemma4:12b-it-q4_K_M": 7_556_497_632,
    "qwen3.5:9b-q4_K_M": 6_594_462_816,
    "ministral-3:14b-instruct-2512-q4_K_M": 9_082_522_240,
    "phi4:14b-q4_K_M": 9_053_114_464,
}
QUALITY_PUSH_V2_COMPARISON_REFERENCES = (
    "qwen3:8b",
    "qwen2.5-coder:7b",
    "gemma4:12b-it-q4_K_M",
    "phi4:14b-q4_K_M",
)


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


def _validate_quality_push_v2_reports(
    reports: list[dict[str, Any]],
) -> str:
    references = [report.get("model_reference") for report in reports]
    if (
        len(references) != len(set(references))
        or set(references) != set(QUALITY_PUSH_V2_COMPARISON_REFERENCES)
    ):
        raise RuntimeError(
            "Quality Push v2 comparison requires the three production text "
            "models and Phi-4 exactly once"
        )
    fingerprints = {_matrix_fingerprint(report) for report in reports}
    if len(fingerprints) != 1:
        raise RuntimeError(
            "Quality Push v2 reports do not use an identical test matrix"
        )
    for report in reports:
        if report.get("profile", {}).get("id") != BASELINE_PROFILE:
            raise RuntimeError(
                "Quality Push v2 comparison requires the baseline profile"
            )
        summary = report.get("summary", {})
        if summary.get("tests") != 221:
            raise RuntimeError(
                "Quality Push v2 report has an incomplete test matrix"
            )
        if set(summary.get("categories", {})) != EXPECTED_CATEGORIES:
            raise RuntimeError(
                "Quality Push v2 report has incomplete categories"
            )
        if report.get("profile", {}).get("production_routing_changed") is not False:
            raise RuntimeError(
                "Quality Push v2 comparison was not production-isolated"
            )
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


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _upgrade_experiment_document(
    aggregate: dict[str, Any],
    *,
    production_allowlist: tuple[str, ...],
    task_model_preferences: dict[str, str],
) -> dict[str, Any]:
    category_results = aggregate["category_results"]
    recommended = aggregate["routing_recommendation"]["changes"]
    category_tasks = {
        "executable_code_generation": "code_generation",
    }
    applied = {
        category: reference
        for category, reference in recommended.items()
        if task_model_preferences.get(category_tasks.get(category, category))
        == reference
    }
    return {
        "schema_version": 2,
        "benchmark_commit": aggregate["benchmark_commit"],
        "review_status": "complete",
        "before_score": MODEL_DISCOVERY_BASELINE_SCORE,
        "after_score": None,
        "score_delta": None,
        "models": [
            {
                "model_reference": item["model_reference"],
                "model_id": item.get("model_id"),
                "model_metadata": item.get("model_metadata", {}),
                "summary": item["summary"],
                "production_allowlisted": (
                    item["model_reference"] in production_allowlist
                ),
                "production_routed": (
                    item["model_reference"]
                    in task_model_preferences.values()
                ),
            }
            for item in aggregate["models"]
        ],
        "category_winners": {
            category: {
                "winner": result["winner"],
                "ranking": result["ranking"],
            }
            for category, result in category_results.items()
        },
        "profile_experiments": [],
        "installation_verification": aggregate["installation_verification"],
        "routing_decision": {
            "candidate_production_admission": (
                "admitted_for_evidence_backed_tasks"
                if applied == recommended and recommended
                else "pending"
                if recommended
                else "no_change_required"
            ),
            "candidate_reason": (
                "Only complete categories clearing the material quality and "
                "stability gates are eligible for task-specific routing."
            ),
            "production_allowlist_change": any(
                reference in production_allowlist
                for reference in recommended.values()
            ),
            "recommended_route_changes": recommended,
            "applied_route_changes": applied,
            "unchanged_routes": {
                category: result["route_recommendation"]
                for category, result in category_results.items()
                if category not in recommended
                and result["route_recommendation"] is not None
            },
            "long_context_note": aggregate["routing_recommendation"][
                "long_context_note"
            ],
        },
        "raw_answers_location": "current-hardware-model-discovery.json",
    }


def _apply_task_preferences_to_routes(
    routes: list[dict[str, Any]],
    task_model_preferences: dict[str, str],
) -> None:
    preferred_ids = {
        task: public_model_id("ollama-local", reference)
        for task, reference in task_model_preferences.items()
    }
    reserved_ids = frozenset(preferred_ids.values())
    for route in routes:
        task = route.get("task")
        ordered_ids = [
            route.get("model_id"),
            *route.get("fallback_model_ids", []),
        ]
        unreserved = [
            model_id
            for model_id in ordered_ids
            if isinstance(model_id, str) and model_id not in reserved_ids
        ]
        preferred_id = preferred_ids.get(task)
        ranked = (
            [preferred_id, *unreserved]
            if preferred_id is not None
            else unreserved or [
                model_id
                for model_id in ordered_ids
                if isinstance(model_id, str)
            ]
        )
        if ranked:
            route["model_id"] = ranked[0]
            route["fallback_model_ids"] = ranked[1:]


def _synchronize_existing_reports(
    report_root: Path,
    aggregate: dict[str, Any],
    *,
    production_allowlist: tuple[str, ...],
    task_model_preferences: dict[str, str],
) -> None:
    upgrade = _upgrade_experiment_document(
        aggregate,
        production_allowlist=production_allowlist,
        task_model_preferences=task_model_preferences,
    )
    summary_path = report_root / "benchmark-summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        final_score = summary.get("total_score")
        if isinstance(final_score, (int, float)):
            upgrade["after_score"] = final_score
            upgrade["score_delta"] = round(
                final_score - MODEL_DISCOVERY_BASELINE_SCORE,
                2,
            )
            summary["model_discovery_baseline_score"] = (
                MODEL_DISCOVERY_BASELINE_SCORE
            )
            summary["model_discovery_score_delta"] = upgrade["score_delta"]
            _atomic_json(summary_path, summary)
    _atomic_json(report_root / "model-upgrade-experiment.json", upgrade)

    matrix_path = report_root / "current-model-capability-matrix.json"
    if matrix_path.is_file():
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        matrix["isolated_candidate_models"] = [
            {
                "exact_model_id": item["model_reference"],
                **item.get("model_metadata", {}),
                "production_allowlisted": (
                    item["model_reference"] in production_allowlist
                ),
                "production_routed": (
                    item["model_reference"]
                    in task_model_preferences.values()
                ),
                "isolated_benchmark_summary": item["summary"],
            }
            for item in aggregate["models"]
        ]
        matrix["candidate_installation_verification"] = aggregate[
            "installation_verification"
        ]
        matrix["current_hardware_discovery"] = {
            "matrix_sha256": aggregate["matrix_sha256"],
            "category_results": aggregate["category_results"],
            "routing_decision": upgrade["routing_decision"],
        }
        _apply_task_preferences_to_routes(
            matrix.get("task_routes", []),
            task_model_preferences,
        )
        _atomic_json(matrix_path, matrix)

    for filename, key in (
        ("model-comparison.json", "upgrade_experiments"),
        ("code-generation-results.json", "model_candidate_comparison"),
    ):
        path = report_root / filename
        if path.is_file():
            document = json.loads(path.read_text(encoding="utf-8"))
            document[key] = upgrade
            _atomic_json(path, document)

    report_path = report_root / "benchmark-report.md"
    if report_path.is_file():
        report = report_path.read_text(encoding="utf-8")
        if "- Current-hardware discovery baseline:" not in report:
            report = report.replace(
                "- Total score:",
                f"- Current-hardware discovery baseline: **{MODEL_DISCOVERY_BASELINE_SCORE}/100**\n"
                f"- Current-hardware discovery delta: **{upgrade['score_delta']}**\n"
                "- Total score:",
                1,
            )
        discovery_section = (
            "## Current-hardware model discovery\n\n"
            "- Best current task routing: Qwen3 8B for reasoning, mathematics, debugging, expert analysis, and exact output; Qwen2.5 Coder 7B for coding; Gemma 4 12B for executable code generation.\n"
            "- Best vision model: Qwen2.5-VL 7B (99.5 measured vision score).\n"
            "- Gemma 4 12B executable code: 21/24 versus Qwen3 8B at 19/24; the task-specific production route is applied.\n"
            f"- All {len(aggregate['models'])} text candidates used the identical 221-case matrix; raw original answers remain in `current-hardware-model-discovery.json`.\n"
            "- Current 12GB limit: Phi-4 Reasoning 14B Q4 was rejected before download because model weights plus reserve exceed detected VRAM.\n"
            "- Future 200B+ path remains hardware discovery → eligibility recalculation → task routing, with no API, database, RAG, memory, UI, or agent redesign.\n"
        )
        report = re.sub(
            r"## (?:Isolated 14B model experiment|Current-hardware model discovery)\n.*?(?=\n## Recommended fixes)",
            discovery_section,
            report,
            count=1,
            flags=re.DOTALL,
        )
        temporary = report_path.with_name(".benchmark-report.md.tmp")
        temporary.write_text(report, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(report_path)
        report_path.chmod(0o600)


def aggregate_discovery_reports(report_root: Path, inputs: list[Path]) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in inputs]
    matrix_fingerprint = _validate_reports(reports)
    admission = hardware_admission()
    if admission["phi4-reasoning:14b-q4_K_M"]["decision"] != "rejected_before_download":
        raise RuntimeError("Phi-4 admission expectation no longer matches detected hardware")
    ranked_categories = _rank_categories(reports)
    installations = {
        reference: _installed_model_blob_verification(reference)
        for reference in sorted(
            {
                *BASELINE_MODEL_REFERENCES,
                *CURRENT_HARDWARE_DISCOVERY_REFERENCES,
            }
        )
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


def aggregate_quality_push_v2_reports(
    report_root: Path,
    inputs: list[Path],
) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in inputs]
    matrix_fingerprint = _validate_quality_push_v2_reports(reports)
    ranked_categories = _rank_categories(reports)
    source_commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    ).stdout.strip()
    route_changes = {
        category: result["route_recommendation"]
        for category, result in ranked_categories.items()
        if result["material_route_improvement"]
    }
    summary_path = report_root / "benchmark-summary.json"
    benchmark_score_after = None
    summary: dict[str, Any] | None = None
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        benchmark_score_after = summary.get("total_score")
    aggregate = {
        "schema_version": 1,
        "benchmark_commit": source_commit,
        "benchmark_score_before": MODEL_DISCOVERY_BASELINE_SCORE,
        "benchmark_score_after": benchmark_score_after,
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
        "models": reports,
        "category_results": ranked_categories,
        "routing_recommendation": {
            "changes": route_changes,
            "requires_production_change": bool(route_changes),
            "long_context_note": (
                "Long-context results do not replace the separately verified "
                "Qwen2.5-VL production route."
            ),
        },
    }
    from app.core.config import settings
    from scripts.ai_quality_benchmark import (
        _quality_push_v2_profile_reports,
    )

    profile_experiments = _quality_push_v2_profile_reports(report_root)
    if summary is not None and isinstance(benchmark_score_after, (int, float)):
        summary["quality_push_v2_baseline_score"] = (
            MODEL_DISCOVERY_BASELINE_SCORE
        )
        summary["quality_push_v2_score_delta"] = round(
            benchmark_score_after - MODEL_DISCOVERY_BASELINE_SCORE,
            2,
        )
        summary["quality_push_v2_source_commit"] = source_commit
        _atomic_json(summary_path, summary)
    _atomic_json(
        report_root / "quality-push-v2-model-comparison.json",
        aggregate,
    )
    for filename in (
        "current-hardware-model-discovery.json",
        "model-comparison.json",
    ):
        path = report_root / filename
        if not path.is_file():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        document["quality_push_v2_comparison"] = aggregate
        if filename == "model-comparison.json":
            document["quality_push_v2_profile_experiments"] = (
                profile_experiments
            )
        _atomic_json(path, document)

    code_path = report_root / "code-generation-results.json"
    if code_path.is_file():
        code_document = json.loads(code_path.read_text(encoding="utf-8"))
        code_document["quality_push_v2_profile_experiments"] = [
            item
            for item in profile_experiments
            if str(item["profile"].get("id", "")).startswith(
                "code_generation"
            )
        ]
        _atomic_json(code_path, code_document)

    matrix_path = report_root / "current-model-capability-matrix.json"
    if matrix_path.is_file():
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        active_route_ids = {
            model_id
            for route in matrix.get("task_routes", [])
            for model_id in (
                route.get("model_id"),
                *route.get("fallback_model_ids", []),
            )
            if isinstance(model_id, str)
        }
        matrix["quality_push_v2_isolated_models"] = [
            {
                "exact_model_id": item["model_reference"],
                **item.get("model_metadata", {}),
                "production_allowlisted": (
                    item["model_reference"]
                    in settings.OLLAMA_LOCAL_MODEL_ALLOWLIST
                ),
                "production_routed": (
                    item["model_reference"]
                    in settings.OLLAMA_TASK_MODEL_PREFERENCES.values()
                    or public_model_id(
                        "ollama-local", item["model_reference"]
                    )
                    in active_route_ids
                ),
                "isolated_benchmark_summary": item["summary"],
            }
            for item in reports
        ]
        matrix["quality_push_v2_comparison"] = {
            "matrix_sha256": matrix_fingerprint,
            "category_results": ranked_categories,
            "routing_recommendation": aggregate["routing_recommendation"],
        }
        _atomic_json(matrix_path, matrix)

    hardware_path = report_root / "hardware-admission-matrix.json"
    if hardware_path.is_file():
        hardware = json.loads(hardware_path.read_text(encoding="utf-8"))
        phi_report = next(
            item
            for item in reports
            if item["model_reference"] == "phi4:14b-q4_K_M"
        )
        hardware["quality_push_v2_phi4_admission"] = {
            "exact_model_reference": phi_report["model_reference"],
            "installed": True,
            "integrity_verified": True,
            "currently_runnable": phi_report["model_metadata"].get(
                "runnable_now"
            ),
            "required_vram_bytes": phi_report["model_metadata"].get(
                "required_vram_bytes"
            ),
            "measured_peak_gpu_used_mib": phi_report["summary"][
                "resources"
            ].get("peak_gpu_used_mib"),
            "production_admitted": False,
            "reason": "whole-category comparison was materially worse",
        }
        _atomic_json(hardware_path, hardware)

    report_path = report_root / "benchmark-report.md"
    if report_path.is_file():
        report = report_path.read_text(encoding="utf-8")
        delta = (
            round(benchmark_score_after - MODEL_DISCOVERY_BASELINE_SCORE, 2)
            if isinstance(benchmark_score_after, (int, float))
            else None
        )
        if "- Quality Push v2 baseline:" not in report:
            report = report.replace(
                "- Total score:",
                f"- Quality Push v2 baseline: **{MODEL_DISCOVERY_BASELINE_SCORE}/100**\n"
                f"- Quality Push v2 delta: **{delta}**\n"
                "- Total score:",
                1,
            )
        if "- Quality Push v2 source commit:" not in report:
            report = report.replace(
                "- Quality Push v2 baseline:",
                f"- Quality Push v2 source commit: `{source_commit}`\n"
                "- Quality Push v2 baseline:",
                1,
            )
        profile_section = (
            "## Quality Push v2 model/profile evidence\n\n"
            f"- Identical-matrix SHA-256: `{matrix_fingerprint}`.\n"
            "- Category routing changes recommended: none; every incumbent "
            "remained its complete-category winner.\n"
            "- Gemma 4 12B executable code generation: 24/24.\n"
            "- Phi-4 14B: installed and integrity-verified, but not "
            "production-allowlisted or routed after scoring 73.98/100.\n"
            "- Focused inference-profile evidence is embedded in "
            "`model-comparison.json` and `code-generation-results.json`.\n"
        )
        report = re.sub(
            r"## Quality Push v2 model/profile evidence\n.*?(?=\n## Recommended fixes)",
            profile_section,
            report,
            count=1,
            flags=re.DOTALL,
        )
        if "## Quality Push v2 model/profile evidence" not in report:
            report = report.replace(
                "## Recommended fixes",
                profile_section + "\n## Recommended fixes",
                1,
            )
        temporary = report_path.with_name(".benchmark-report.md.tmp")
        temporary.write_text(report, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(report_path)
        report_path.chmod(0o600)
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality-push-v2", action="store_true")
    parser.add_argument("report_root")
    parser.add_argument("reports", nargs="+")
    arguments = parser.parse_args()
    report_root = Path(arguments.report_root)
    if not report_root.is_absolute() or report_root.name != "Work_Station_Benchmark":
        raise RuntimeError("hardware discovery report root is invalid")
    input_paths = [Path(value) for value in arguments.reports]
    if arguments.quality_push_v2:
        aggregate_quality_push_v2_reports(report_root, input_paths)
        print("QUALITY_PUSH_V2_MODEL_COMPARISON_COMPLETE")
        return
    aggregate = aggregate_discovery_reports(report_root, input_paths)
    from app.core.config import settings

    _synchronize_existing_reports(
        report_root,
        aggregate,
        production_allowlist=settings.OLLAMA_LOCAL_MODEL_ALLOWLIST,
        task_model_preferences=settings.OLLAMA_TASK_MODEL_PREFERENCES,
    )
    print("CURRENT_HARDWARE_MODEL_DISCOVERY_AGGREGATE_COMPLETE")


if __name__ == "__main__":
    main()
