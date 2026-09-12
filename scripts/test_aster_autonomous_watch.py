#!/usr/bin/env python3
"""Focused tests for the persisted external-gap watch handoff."""

from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import aster_next_prompt as controller


def _observations() -> dict:
    return {
        "benchmark": {"score": 97.84, "pass": 457, "partial": 1, "fail": 1},
        "runtime": {"status": "PASS"},
        "provenance": {"status": "PASS"},
        "git": {"status": "CLEAN_SYNCED", "commit": "a" * 40, "application_source_commit": "b" * 40},
        "tests": {},
    }


def test_terminal_queue_enters_watch():
    state = {"overall_ready": False, "next_prompt": "wait for candidate"}
    queue = {"issues": [{"id": "ASTER-006", "status": "BLOCKED_EXTERNAL"}]}
    assert controller.terminal_queue_requires_external_watch(state, queue, _observations())


def test_host_controller_candidate_set_is_exact_and_excludes_tested_model():
    assert controller.watch_candidate_references({"external_watch": {"candidate_priority": ["wrong:1b"]}}) == [
        "qwen2.5-coder:3b",
        "qwen2.5-coder:1.5b",
        "deepseek-coder:1.3b",
        "starcoder2:3b",
        "codegemma:2b",
    ]


def test_discovery_freshly_probes_all_exact_candidates_and_requires_both_limits():
    references = list(controller.HOST_CONTROLLER_CANDIDATES)
    probed = []

    def fake_local(reference: str) -> dict:
        return {"reference": reference, "available": False}

    def fake_manifest(reference: str) -> dict:
        return {
            "reference": reference,
            "status": "AVAILABLE",
            "model_layer_digest": "sha256:" + "a" * 64,
        }

    def fake_probe(manifest: dict) -> dict:
        probed.append(manifest["reference"])
        return {
            "status": "AVAILABLE",
            "sha256_addressed": True,
            "estimated_download_hours": 0.4,
            "estimated_download_seconds": 1000,
        }

    with patch.object(controller, "local_ollama_candidate", side_effect=fake_local), patch.object(
        controller, "remote_model_manifest", side_effect=fake_manifest
    ), patch.object(controller, "remote_range_probe", side_effect=fake_probe):
        discovery = controller.discover_watch_candidates(
            {}, max_estimated_hours=0.5, max_download_seconds=900
        )

    assert probed == references
    assert len(discovery["remote"]) == 5
    assert discovery["status"] == "NOT_READY"
    assert discovery["candidate_policy"]["constructed_exactly"] is True
    assert discovery["candidate_policy"]["excluded_reference_absent"] is True
    assert all(
        item["qualification"]["qualifies"] is False
        for item in discovery["remote"]
    )


def test_focused_gate_requires_exactly_eight_objective_pass_values():
    routes = sorted(controller.FOCUSED_ADMISSION_ROUTES)
    tests = sorted(controller.FOCUSED_ADMISSION_TESTS)
    records = [
        {"route": route, "test_id": test_id, "objective_pass": True}
        for route in routes
        for test_id in tests
    ]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "focused-candidate-gate.json"
        path.write_text(
            __import__("json").dumps(
                {
                    "run_status": "complete",
                    "candidate_reference": "qwen2.5-coder:3b",
                    "matrix_complete": True,
                    "records": records,
                }
            ),
            encoding="utf-8",
        )
        passing = controller.focused_objective_gate(
            Path(directory), "qwen2.5-coder:3b"
        )
        assert passing["admitted"] is True
        records[-1]["objective_pass"] = False
        path.write_text(
            __import__("json").dumps(
                {
                    "run_status": "complete",
                    "candidate_reference": "qwen2.5-coder:3b",
                    "matrix_complete": True,
                    "records": records,
                }
            ),
            encoding="utf-8",
        )
        failing = controller.focused_objective_gate(
            Path(directory), "qwen2.5-coder:3b"
        )
        assert failing["exact_eight_record_matrix"] is True
        assert failing["objective_pass_count"] == 7
        assert failing["admitted"] is False


def test_no_qualifying_candidate_persists_not_ready_without_child():
    state = {
        "iteration": 4,
        "attempt": 26,
        "overall_ready": False,
        "next_prompt": "host recovery",
        "external_watch": {"watch_interval_seconds": 0},
        "history": [],
    }
    discovery = {
        "status": "NOT_READY",
        "candidate": "qwen2.5-coder:3b",
        "candidate_state": "NOT_CACHED_DOWNLOAD_TOO_SLOW_OR_UNAVAILABLE",
        "candidate_policy": {
            "candidate_references": list(controller.HOST_CONTROLLER_CANDIDATES),
            "excluded_reference_absent": True,
        },
        "local": [],
        "remote": [],
    }
    with TemporaryDirectory() as directory, patch.object(
        controller, "current_queue", return_value={"issues": []}
    ), patch.object(
        controller, "observe", return_value=_observations()
    ), patch.object(
        controller, "discover_watch_candidates", return_value=discovery
    ), patch.object(
        controller, "run_codex_child"
    ) as child, patch.object(
        controller, "render_reports", return_value={}
    ), patch.object(
        controller,
        "persist_watch_report_commit",
        return_value={"attempted": False},
    ):
        result = controller.watch_external_iteration(
            state,
            evidence_root=Path(directory),
            cycle=1,
            execute_codex=True,
            timeout_seconds=30,
            max_download_seconds=900,
            max_estimated_hours=0.5,
        )

    child.assert_not_called()
    assert result["status"] == "NOT_READY"
    assert result["classification"] == "NOT_READY"
    assert state["in_progress"] is False
    assert state["resume_required"] is False


def test_candidate_child_handoff_repeats_without_copy_paste():
    state = {
        "iteration": 4,
        "attempt": 26,
        "overall_ready": False,
        "next_prompt": "wait for candidate",
        "external_watch": {"watch_interval_seconds": 0},
        "history": [],
    }
    child_prompts = []
    child_outputs = [
        "Run the generic coding subset with the admitted candidate.",
        "Run the unchanged canonical benchmark and final gates.",
    ]

    def fake_child(prompt: str, iteration_dir: Path, *, timeout_seconds: int, evidence_root: Path) -> dict:
        child_prompts.append(prompt)
        return {
            "command": ["codex", "exec"],
            "cwd": str(controller.REPOSITORY_ROOT),
            "exit_code": 0,
            "timed_out": False,
            "parsed": controller.parse_result_text(
                "ASTER_LOOP_RESULT\nSTATUS=NOT_READY\nISSUES_REMAINING=0\nCURRENT_ISSUE=NONE\n"
                "ACTION=handoff\nVERIFICATION=PARTIAL\nNEXT_PROMPT_BEGIN\n"
                f"{child_outputs[len(child_prompts) - 1]}\nNEXT_PROMPT_END\n"
            ),
        }

    candidate = {
        "status": "CANDIDATE_AVAILABLE",
        "candidate": "qwen2.5-coder:3b",
        "candidate_state": "INSTALLED_RUNNABLE_NOW",
        "local": [],
        "remote": [],
    }
    with TemporaryDirectory() as directory, patch.object(controller, "current_queue", return_value={"issues": []}), patch.object(
        controller, "observe", return_value=_observations()
    ), patch.object(controller, "discover_watch_candidates", return_value=candidate), patch.object(
        controller, "run_codex_child", side_effect=fake_child
    ), patch.object(
        controller,
        "trusted_parent_focused_gate",
        return_value={"trusted_parent": True, "run_status": "complete", "all_eight_objective_pass": True},
    ), patch.object(
        controller,
        "focused_objective_gate",
        return_value={"admitted": True, "all_eight_objective_pass": True},
    ), patch.object(controller, "render_reports", return_value={}):
        first = controller.watch_external_iteration(
            state,
            evidence_root=Path(directory),
            cycle=1,
            execute_codex=True,
            timeout_seconds=30,
            max_download_seconds=900,
            max_estimated_hours=0.5,
        )
        assert first["child"]["parsed"]["next_prompt"] == child_outputs[0]
        assert state["next_prompt"] == child_outputs[0]
        assert state["controller_phase"] == "CANDIDATE_FOCUSED_GATE"

        second = controller.watch_external_iteration(
            state,
            evidence_root=Path(directory),
            cycle=2,
            execute_codex=True,
            timeout_seconds=30,
            max_download_seconds=900,
            max_estimated_hours=0.5,
        )
        assert second["child"]["parsed"]["next_prompt"] == child_outputs[1]
        assert state["next_prompt"] == child_outputs[1]

    assert len(child_prompts) == 2
    assert "medium-coding-04" in child_prompts[0]
    assert "model-comparison-coder-06" in child_prompts[0]
    assert child_prompts[1] == child_outputs[0]


def test_host_recovery_prompt_is_not_dispatched_even_for_current_source():
    current_commit = "b" * 40
    state = {
        "iteration": 4,
        "attempt": 27,
        "overall_ready": False,
        "next_prompt": "ASTER HOST-CONTROLLER-ONLY CANDIDATE RECOVERY",
        "next_prompt_source": "child_structured_result",
        "next_prompt_source_commit": current_commit,
        "external_watch": {"watch_interval_seconds": 0},
        "history": [],
    }
    child_prompts = []

    def fake_child(prompt: str, iteration_dir: Path, *, timeout_seconds: int, evidence_root: Path) -> dict:
        child_prompts.append(prompt)
        return {
            "command": ["codex", "exec"],
            "cwd": str(controller.REPOSITORY_ROOT),
            "exit_code": 0,
            "timed_out": False,
            "parsed": controller.parse_result_text(
                "ASTER_LOOP_RESULT\nSTATUS=NOT_READY\nISSUES_REMAINING=0\nCURRENT_ISSUE=NONE\n"
                "ACTION=retry\nVERIFICATION=BLOCKED\nNEXT_PROMPT_BEGIN\n"
                f"Current source commit: {current_commit}\nNEXT_PROMPT_END\n"
            ),
        }

    candidate = {
        "status": "CANDIDATE_AVAILABLE",
        "candidate": "qwen2.5-coder:3b",
        "candidate_state": "INSTALLED_RUNNABLE_NOW",
        "local": [],
        "remote": [],
    }
    with TemporaryDirectory() as directory, patch.object(controller, "current_queue", return_value={"issues": []}), patch.object(
        controller, "observe", return_value={**_observations(), "git": {**_observations()["git"], "application_source_commit": current_commit}}
    ), patch.object(controller, "discover_watch_candidates", return_value=candidate), patch.object(
        controller, "run_codex_child", side_effect=fake_child
    ), patch.object(
        controller,
        "trusted_parent_focused_gate",
        return_value={"trusted_parent": True, "run_status": "complete", "all_eight_objective_pass": True},
    ), patch.object(
        controller,
        "focused_objective_gate",
        return_value={"admitted": True, "all_eight_objective_pass": True},
    ), patch.object(controller, "render_reports", return_value={}):
        controller.watch_external_iteration(
            state,
            evidence_root=Path(directory),
            cycle=1,
            execute_codex=True,
            timeout_seconds=30,
            max_download_seconds=900,
            max_estimated_hours=0.5,
        )

    assert len(child_prompts) == 1
    assert "HOST-CONTROLLER-ONLY" not in child_prompts[0]
    assert child_prompts[0].startswith("ASTER CANDIDATE ADMISSION — ")
    assert current_commit in child_prompts[0]
    assert state["next_prompt_source_commit"] == current_commit


def main() -> int:
    test_terminal_queue_enters_watch()
    test_host_controller_candidate_set_is_exact_and_excludes_tested_model()
    test_discovery_freshly_probes_all_exact_candidates_and_requires_both_limits()
    test_focused_gate_requires_exactly_eight_objective_pass_values()
    test_no_qualifying_candidate_persists_not_ready_without_child()
    test_candidate_child_handoff_repeats_without_copy_paste()
    print("test_aster_autonomous_watch: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
