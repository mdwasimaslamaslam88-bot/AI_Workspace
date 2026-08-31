from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.current_hardware_model_discovery import MATERIAL_SCORE_DELTA
from scripts.model_candidate_benchmark import (
    CURRENT_HARDWARE_VISION_REFERENCES,
    VISION_PROFILE,
)


CURRENT_VISION_REFERENCE = "qwen2.5vl:7b"


def _fingerprint(report: dict[str, Any]) -> str:
    matrix = [
        {
            "test_id": result.get("test_id"),
            "prompt": result.get("prompt"),
            "expected_behavior": result.get("expected_behavior"),
        }
        for result in report.get("results", [])
    ]
    return hashlib.sha256(
        json.dumps(matrix, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def aggregate_vision_reports(report_root: Path, inputs: list[Path]) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in inputs]
    references = [report.get("model_reference") for report in reports]
    if len(references) != len(set(references)) or set(references) != set(
        CURRENT_HARDWARE_VISION_REFERENCES
    ):
        raise RuntimeError("vision discovery requires every approved vision model once")
    failed_reports = [
        report for report in reports if report.get("run_status", "complete") == "failed"
    ]
    for report in failed_reports:
        if (
            report.get("model_reference") == CURRENT_VISION_REFERENCE
            or report.get("failure", {}).get("code")
            not in {"gpu_thermal_guard", "gpu_vram_guard", "ram_guard"}
        ):
            raise RuntimeError("vision discovery rejected an invalid failed report")
    complete_reports = [report for report in reports if report not in failed_reports]
    if len({_fingerprint(report) for report in complete_reports}) != 1:
        raise RuntimeError("vision discovery reports do not use an identical matrix")
    for report in complete_reports:
        if report.get("profile", {}).get("id") != VISION_PROFILE:
            raise RuntimeError("vision discovery report has the wrong profile")
        summary = report.get("summary", {})
        if summary.get("tests") != 7 or set(summary.get("categories", {})) != {"vision"}:
            raise RuntimeError("vision discovery report is incomplete")
        if summary.get("stability", {}).get("request_failures") != 0:
            raise RuntimeError("vision discovery report contains request failures")
    ranking = sorted(
        (
            {
                "model_reference": report["model_reference"],
                **report["summary"]["categories"]["vision"],
            }
            for report in complete_reports
        ),
        key=lambda item: (
            -item["score"],
            -item["pass"],
            item["fail"],
            item["average_latency_seconds"],
            item["model_reference"],
        ),
    )
    by_reference = {item["model_reference"]: item for item in ranking}
    current = by_reference[CURRENT_VISION_REFERENCE]
    winner = ranking[0]
    material = bool(
        winner["model_reference"] != CURRENT_VISION_REFERENCE
        and winner["score"] >= current["score"] + MATERIAL_SCORE_DELTA
        and winner["pass"] >= current["pass"]
        and winner["fail"] <= current["fail"]
    )
    aggregate = {
        "schema_version": 1,
        "production_changed_during_isolated_benchmark": False,
        "synthetic_non_sensitive_assets_only": True,
        "models": complete_reports,
        "hardware_safety_rejections": [
            {
                "model_reference": report["model_reference"],
                "failure": report["failure"],
                "last_resource_sample": report.get("last_resource_sample"),
            }
            for report in failed_reports
        ],
        "ranking": ranking,
        "best_measured_model": winner["model_reference"],
        "current_production_model": CURRENT_VISION_REFERENCE,
        "score_delta_from_current": round(winner["score"] - current["score"], 2),
        "material_route_improvement": material,
        "route_recommendation": (
            winner["model_reference"] if material else CURRENT_VISION_REFERENCE
        ),
    }
    serialized = json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n"
    output = report_root / "current-hardware-vision-discovery.json"
    temporary = report_root / ".current-hardware-vision-discovery.json.tmp"
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
        raise RuntimeError("vision discovery report root is invalid")
    aggregate_vision_reports(report_root, [Path(value) for value in arguments.reports])
    print("CURRENT_HARDWARE_VISION_DISCOVERY_AGGREGATE_COMPLETE")


if __name__ == "__main__":
    main()
