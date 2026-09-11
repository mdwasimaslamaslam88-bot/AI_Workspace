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
        "candidate_state": "AVAILABLE_LOCAL",
        "local": [],
        "remote": [],
    }
    with TemporaryDirectory() as directory, patch.object(controller, "current_queue", return_value={"issues": []}), patch.object(
        controller, "observe", return_value=_observations()
    ), patch.object(controller, "discover_watch_candidates", return_value=candidate), patch.object(
        controller, "run_codex_child", side_effect=fake_child
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
    assert all("medium-coding-04" in prompt and "model-comparison-coder-06" in prompt for prompt in child_prompts)


def main() -> int:
    test_terminal_queue_enters_watch()
    test_candidate_child_handoff_repeats_without_copy_paste()
    print("test_aster_autonomous_watch: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
