"""Focused tests for ASTER's current-evidence provenance gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = REPOSITORY_ROOT / "scripts/aster_next_prompt.py"


def _load_controller():
    spec = importlib.util.spec_from_file_location("aster_controller", CONTROLLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _current_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, text=True
    ).strip()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path):
    controller = _load_controller()
    commit = _current_commit()
    benchmark = tmp_path / "benchmark" / "Work_Station_Benchmark"
    rows = [{"test_id": "case-1", "result": "PASS"}]
    _write(benchmark / "benchmark-results.json", {"results": rows})
    _write(
        benchmark / "benchmark-summary.json",
        {
            "status": "BENCHMARK COMPLETE",
            "git_commit": commit,
            "number_of_tests": 1,
            "counts": {"PASS": 1, "PARTIAL": 0, "FAIL": 0},
        },
    )
    runtime = tmp_path / "runtime-identity-current.json"
    backend_hash = controller.tree_digest(
        REPOSITORY_ROOT / "backend" / "app",
        suffixes=frozenset({".py", ".json"}),
    )
    web_hash = controller.tree_digest(REPOSITORY_ROOT / "frontend" / "dist")
    _write(
        runtime,
        {
            "runtime": {
                "source_commit": commit,
                "backend_source_sha256": backend_hash,
                "web_bundle_sha256": web_hash,
            },
            "matches": {
                "source_commit": True,
                "backend_source_sha256": True,
                "web_bundle_sha256": True,
            },
        },
    )
    release_log = tmp_path / "release-check.log"
    release_log.write_text("WORK STATION release validation passed\n", encoding="utf-8")
    _write(
        tmp_path / "release-check-current.json",
        {
            "commit": commit,
            "exit_code": 0,
            "status": "PASS",
            "output_path": str(release_log),
            "output_sha256": hashlib.sha256(release_log.read_bytes()).hexdigest(),
            "checks": {"security": "PASS; npm audit found 0 vulnerabilities"},
        },
    )
    artifact = tmp_path / "final-release-attestation.json"
    artifact_files = []
    for name, payload in (("app.bin", b"current app\n"), ("pwa.tar.gz", b"current pwa\n")):
        artifact_path = tmp_path / "artifacts" / name
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(payload)
        artifact_files.append(
            {
                "artifact": name,
                "path": str(artifact_path),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "exists": True,
                "hash_verified": True,
            }
        )
    _write(
        artifact,
        {
            "final_report_commit": commit,
            "artifact_hashes_verified": {"app": True, "pwa": True},
            "artifacts": artifact_files,
        },
    )
    observations = {
        "benchmark": {"path": str(benchmark / "benchmark-summary.json")},
        "runtime": {"evidence": str(runtime)},
    }
    status = {"release": {"final_report_tip_attestation": str(artifact)}}
    return controller, commit, observations, status, runtime, benchmark, artifact


def test_current_provenance_accepts_complete_current_evidence(tmp_path):
    controller, commit, observations, status, *_ = _fixture(tmp_path)
    result = controller.validate_current_provenance(
        tmp_path, observations, status, commit
    )

    assert result["status"] == "PASS"
    assert all(value == "PASS" for value in result["components"].values())
    assert result["runtime"]["content_matches"] == {
        "source_commit": True,
        "backend_source_sha256": True,
        "web_bundle_sha256": True,
        "declared_matches": True,
    }


def test_current_provenance_rejects_stale_commit_claims(tmp_path):
    controller, commit, observations, status, runtime, benchmark, artifact = _fixture(tmp_path)

    runtime_value = json.loads(runtime.read_text(encoding="utf-8"))
    runtime_value["runtime"]["source_commit"] = "0" * 40
    runtime.write_text(json.dumps(runtime_value) + "\n", encoding="utf-8")
    result = controller.validate_current_provenance(tmp_path, observations, status, commit)
    assert result["status"] == "NOT_PROVEN"
    assert result["components"]["runtime"] == "HISTORICAL_PASS_REQUIRES_CURRENT_PROVENANCE"

    runtime_value["runtime"]["source_commit"] = commit
    runtime.write_text(json.dumps(runtime_value) + "\n", encoding="utf-8")
    summary_path = benchmark / "benchmark-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["git_commit"] = "1" * 40
    summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
    result = controller.validate_current_provenance(tmp_path, observations, status, commit)
    assert result["components"]["benchmark"] == "HISTORICAL_PASS_REQUIRES_CURRENT_PROVENANCE"

    summary["git_commit"] = commit
    summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
    release_path = tmp_path / "release-check-current.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["commit"] = "2" * 40
    release_path.write_text(json.dumps(release) + "\n", encoding="utf-8")
    result = controller.validate_current_provenance(tmp_path, observations, status, commit)
    assert result["components"]["release"] == "HISTORICAL_PASS_REQUIRES_CURRENT_PROVENANCE"

    release["commit"] = commit
    release_path.write_text(json.dumps(release) + "\n", encoding="utf-8")
    artifact_value = json.loads(artifact.read_text(encoding="utf-8"))
    artifact_value["final_report_commit"] = "3" * 40
    artifact.write_text(json.dumps(artifact_value) + "\n", encoding="utf-8")
    result = controller.validate_current_provenance(tmp_path, observations, status, commit)
    assert result["components"]["artifact"] == "HISTORICAL_PASS_REQUIRES_CURRENT_PROVENANCE"


def test_application_source_commit_walks_consecutive_report_tips():
    controller = _load_controller()
    report_tip = _current_commit()
    parent = subprocess.check_output(
        ["git", "rev-parse", f"{report_tip}^"], cwd=REPOSITORY_ROOT, text=True
    ).strip()
    if report_tip == parent:
        raise AssertionError("Git report-tip ancestry did not advance")
    changed = subprocess.check_output(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", report_tip],
        cwd=REPOSITORY_ROOT,
        text=True,
    ).splitlines()
    if changed and all(path.startswith("reports/ASTER_AI_OS_") for path in changed):
        assert controller.application_source_commit(report_tip) == controller.application_source_commit(parent)


def test_report_only_commit_aliases_are_bounded_to_current_source():
    controller = _load_controller()
    report_tip = _current_commit()
    source = controller.application_source_commit(report_tip)
    if source == report_tip:
        raise AssertionError("fixture requires a report-only tip after the source tip")
    aliases = controller.report_only_commit_aliases(report_tip, source)
    assert report_tip in aliases
    assert source not in aliases
    assert controller.report_only_commit_aliases(report_tip, "f" * 40) == ()
