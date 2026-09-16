#!/usr/bin/env python3
"""Focused tests for the persisted external-gap watch handoff."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import os
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


def _file_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _synthetic_watch_repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "synthetic-watch-repository"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "aster-test@example.invalid")
    _git(repo, "config", "user.name", "ASTER isolated test")
    (repo / "application.txt").write_text("synthetic application\n", encoding="utf-8")
    _git(repo, "add", "application.txt")
    _git(repo, "commit", "-m", "synthetic application commit")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_external_download_policy_is_bounded_at_three_hours():
    parser = controller.build_parser()
    args = parser.parse_args(["run"])
    assert controller.DEFAULT_MAX_DOWNLOAD_SECONDS == 10_800
    assert controller.DEFAULT_MAX_ESTIMATED_DOWNLOAD_HOURS == 3.0
    assert args.max_download_seconds == 10_800
    assert args.max_estimated_download_hours == 3.0
    assert parser.parse_args(
        ["run", "--max-download-seconds", "10800", "--max-estimated-download-hours", "3.0"]
    ).max_download_seconds == 10_800


def _persist_cycle_race(evidence_root: Path, first_cycle: int, second_cycle: int):
    """Persist two independent snapshots in a specified order."""
    initial = {
        "iteration": 4,
        "attempt": 1,
        "overall_ready": False,
        "external_watch": {
            "cycle": 10,
            "evidence": "/evidence/cycle-0010",
            "current_action": "cycle 10 action",
            "rejected_candidates": [],
        },
        "history": [],
    }
    controller.persist_state(initial, evidence_root)
    disk_advancer = controller.load_or_create_state(evidence_root)
    stale_writer = controller.load_or_create_state(evidence_root)

    for state, cycle, candidate in (
        (disk_advancer, 12, "qwen3.5:9b-q4_K_M"),
        (stale_writer, 11, "stale:1b"),
    ):
        state["external_watch"].update(
            {
                "cycle": cycle,
                "evidence": f"/evidence/cycle-{cycle:04d}",
                "current_action": f"cycle {cycle} action",
                "rejected_candidates": [candidate],
            }
        )
        state["last_candidate_decision"] = {
            "candidate": candidate,
            "timestamp": f"2026-09-15T16:{cycle:02d}:00+00:00",
            "cycle": cycle,
            "decision": "REJECTED_OBJECTIVE_FOCUSED_GATE",
        }

    states_by_cycle = {12: disk_advancer, 11: stale_writer}
    controller.persist_state(states_by_cycle[first_cycle], evidence_root)
    controller.persist_state(states_by_cycle[second_cycle], evidence_root)

    recovered = controller.load_or_create_state(evidence_root)
    assert "qwen3.5:9b-q4_K_M" in recovered["external_watch"]["rejected_candidates"]
    assert "stale:1b" in recovered["external_watch"]["rejected_candidates"]
    assert recovered["external_watch"]["cycle"] == 12
    assert recovered["external_watch"]["evidence"] == "/evidence/cycle-0012"
    assert recovered["external_watch"]["current_action"] == "cycle 12 action"
    assert recovered["last_candidate_decision"]["decision"] == "REJECTED_OBJECTIVE_FOCUSED_GATE"
    assert recovered["last_candidate_decision"]["cycle"] == 12

    restarted = controller.load_or_create_state(evidence_root)
    assert "qwen3.5:9b-q4_K_M" in restarted["external_watch"]["rejected_candidates"]
    assert restarted["external_watch"]["cycle"] == 12
    assert restarted["external_watch"]["evidence"] == "/evidence/cycle-0012"


def test_persist_state_cycle_bundle_survives_both_write_orders(tmp_path):
    """The durable cycle/evidence/action tuple is monotonic in either order."""
    for first_cycle, second_cycle in ((12, 11), (11, 12)):
        _persist_cycle_race(tmp_path / f"order-{first_cycle}-{second_cycle}", first_cycle, second_cycle)


def test_acceptance_result_and_exclusion_survive_stale_writer_restart(tmp_path):
    """A stale task snapshot cannot erase a newer trusted result or evidence."""
    for first, second in (("fresh", "stale"), ("stale", "fresh")):
        root = tmp_path / f"task-order-{first}-{second}"
        initial = controller.load_or_create_state(root)
        controller.persist_state(initial, root)
        fresh = controller.load_or_create_state(root)
        stale = controller.load_or_create_state(root)
        fresh_task = fresh["acceptance_tasks"]["ASTER-STATE-MERGE-001"]
        fresh_task.update(
            {
                "status": "COMPLETE",
                "attempts": 2,
                "updated_at": "2026-09-16T00:00:12+00:00",
                "validated_source_commit": "b" * 40,
                "evidence": ["/evidence/cycle-0012"],
                "last_result": {
                    "timestamp": "2026-09-16T00:00:12+00:00",
                    "cycle": 12,
                    "objective_pass": True,
                    "excluded_candidate": "rejected:1b",
                },
            }
        )
        stale_task = stale["acceptance_tasks"]["ASTER-STATE-MERGE-001"]
        stale_task.update(
            {
                "status": "READY",
                "attempts": 1,
                "updated_at": "2026-09-16T00:00:11+00:00",
                "evidence": ["/evidence/cycle-0011"],
                "last_result": None,
            }
        )
        writers = {"fresh": fresh, "stale": stale}
        controller.persist_state(writers[first], root)
        controller.persist_state(writers[second], root)
        recovered = controller.load_or_create_state(root)
        result = recovered["acceptance_tasks"]["ASTER-STATE-MERGE-001"]
        assert result["status"] == "COMPLETE"
        assert result["attempts"] == 2
        assert result["validated_source_commit"] == "b" * 40
        assert result["last_result"]["cycle"] == 12
        assert set(result["evidence"]) == {"/evidence/cycle-0011", "/evidence/cycle-0012"}


def test_source_change_reopens_current_runtime_and_release_gates(tmp_path):
    state = controller.load_or_create_state(tmp_path / "source-refresh")
    old = "a" * 40
    current = "b" * 40
    for task_id in ("ASTER-RUNTIME-PROVENANCE-001", "ASTER-RELEASE-VALIDATION-001"):
        task = state["acceptance_tasks"][task_id]
        task.update({"status": "COMPLETE", "validated_source_commit": old, "updated_at": "2020-01-01T00:00:01+00:00"})
    controller.persist_state(state, tmp_path / "source-refresh")
    stale = controller.load_or_create_state(tmp_path / "source-refresh")
    state = controller.load_or_create_state(tmp_path / "source-refresh")
    assert controller.refresh_source_bound_acceptance_tasks(state, current) is True
    controller.persist_state(state, tmp_path / "source-refresh")
    controller.persist_state(stale, tmp_path / "source-refresh")
    state = controller.load_or_create_state(tmp_path / "source-refresh")
    assert state["acceptance_tasks"]["ASTER-RUNTIME-PROVENANCE-001"]["status"] == "RETRY"
    assert state["acceptance_tasks"]["ASTER-RELEASE-VALIDATION-001"]["status"] == "PENDING"


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


def test_model_reference_accepts_ollama_quantization_case_without_path_chars():
    assert controller._safe_model_reference("qwen3.5:9b-q4_K_M") is True
    assert controller._safe_model_reference("qwen3.5:9b q4_K_M") is False
    assert controller._safe_model_reference("qwen3.5:9b/../../unsafe") is False


def test_codex_resolver_finds_verified_current_cli_with_service_like_path():
    service_path = "/home/md-wasim/.local/bin:/usr/bin"
    with patch.dict(os.environ, {"PATH": service_path}, clear=False):
        os.environ.pop("ASTER_CODEX_BIN", None)
        resolved = controller.resolve_codex_cli()
    assert resolved["version"] == "0.153.4"
    assert "/.nvm/versions/node/" in resolved["path"]


def test_trusted_parent_release_gate_resolves_current_node_under_service_path():
    """Parent release checks must not inherit systemd's stale Node 18 PATH."""
    service_path = "/home/md-wasim/.local/bin:/usr/bin"
    with patch.dict(os.environ, {"PATH": service_path}, clear=False):
        resolved = controller.resolve_node_runtime()
    assert resolved["node_version"] == "24.19.0"
    assert "/.nvm/versions/node/" in resolved["node_path"]
    assert resolved["environment"]["PATH"].startswith(str(Path(resolved["node_path"]).parent))


def test_parent_command_record_can_persist_untruncated_output(tmp_path):
    result = controller.command_record(
        [sys.executable, "-c", "print('stdout-' + 'x' * 6000)"],
        tmp_path,
        timeout=10,
        output_directory=tmp_path / "evidence",
    )
    stdout_path = Path(result["stdout_path"])
    assert result["exit_code"] == 0
    assert stdout_path.read_text(encoding="utf-8").startswith("stdout-")
    assert len(stdout_path.read_text(encoding="utf-8")) > 4000
    assert _file_digest(stdout_path) == result["stdout_sha256"]


def test_runtime_attestation_rejects_non_loopback_origin_without_request(tmp_path):
    with patch.dict(os.environ, {"ASTER_RUNTIME_API_ORIGIN": "https://attacker.invalid"}):
        result = controller.capture_current_runtime_attestation(tmp_path, "a" * 40)
    assert result["objective_pass"] is False
    assert result["error"] == "ValueError"
    assert result["session_revocation"]["error"] == "NO_ACCESS_TOKEN"


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


def test_discovery_skips_candidate_after_complete_objective_gate_rejection():
    references = list(controller.HOST_CONTROLLER_CANDIDATES)
    rejected = "qwen2.5-coder:1.5b"
    probed = []

    def fake_local(reference: str) -> dict:
        return {"reference": reference, "available": False}

    def fake_manifest(reference: str) -> dict:
        return {
            "reference": reference,
            "status": "AVAILABLE",
            "model_layer_digest": "sha256:" + "b" * 64,
        }

    def fake_probe(manifest: dict) -> dict:
        probed.append(manifest["reference"])
        return {
            "status": "AVAILABLE",
            "sha256_addressed": True,
            "estimated_download_hours": 0.4,
            "estimated_download_seconds": 1000,
        }

    state = {"external_watch": {"rejected_candidates": [rejected]}}
    with patch.object(controller, "local_ollama_candidate", side_effect=fake_local), patch.object(
        controller, "remote_model_manifest", side_effect=fake_manifest
    ), patch.object(controller, "remote_range_probe", side_effect=fake_probe):
        discovery = controller.discover_watch_candidates(
            state, max_estimated_hours=0.5, max_download_seconds=900
        )

    assert rejected not in probed
    assert rejected not in discovery["candidate_policy"]["candidate_references"]
    assert rejected in discovery["candidate_policy"]["excluded_references"]
    assert discovery["candidate_policy"]["all_candidate_references"] == references


def test_discovery_skips_candidate_after_incomplete_download():
    rejected = "qwen2.5-coder:1.5b"
    blocked = "qwen2.5-coder:3b"
    with patch.object(
        controller, "local_ollama_candidate", side_effect=lambda reference: {
            "reference": reference, "available": False
        }
    ), patch.object(
        controller,
        "remote_model_manifest",
        side_effect=lambda reference: {
            "reference": reference,
            "status": "AVAILABLE",
            "model_layer_digest": "sha256:" + "c" * 64,
        },
    ), patch.object(
        controller,
        "remote_range_probe",
        side_effect=lambda manifest: {
            "status": "AVAILABLE",
            "estimated_download_hours": 0.4,
            "estimated_download_seconds": 1000,
        },
    ):
        discovery = controller.discover_watch_candidates(
            {
                "external_watch": {
                    "rejected_candidates": [rejected],
                    "acquisition_blocked_candidates": [blocked],
                }
            },
            max_estimated_hours=0.5,
            max_download_seconds=900,
        )

    assert blocked not in discovery["candidate_policy"]["candidate_references"]
    assert blocked in discovery["candidate_policy"]["acquisition_blocked_references"]
    assert rejected in discovery["candidate_policy"]["rejected_references"]


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
        "external_watch": {
            "watch_interval_seconds": 0,
            "rejected_candidates": ["qwen2.5-coder:1.5b"],
        },
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
    assert state["external_watch"]["rejected_candidates"] == ["qwen2.5-coder:1.5b"]


def test_watch_fixture_isolated_from_production_writes_and_processes(tmp_path, monkeypatch):
    """The no-candidate fixture uses only synthetic Git and report roots."""
    isolated_repository, isolated_head = _synthetic_watch_repository(tmp_path)
    monkeypatch.setattr(controller, "REPOSITORY_ROOT", isolated_repository)
    temporary_reports = tmp_path / "reports"
    temporary_evidence = tmp_path / "evidence"
    monkeypatch.setattr(controller, "STATUS_JSON", temporary_reports / "status.json")
    monkeypatch.setattr(controller, "STATUS_MD", temporary_reports / "status.md")
    monkeypatch.setattr(controller, "QUEUE_JSON", temporary_reports / "queue.json")
    monkeypatch.setattr(controller, "PROGRESS_JSON", temporary_reports / "progress.json")
    monkeypatch.setattr(controller, "PROGRESS_MD", temporary_reports / "progress.md")
    isolated_paths = [
        controller.STATUS_JSON,
        controller.STATUS_MD,
        controller.QUEUE_JSON,
        controller.PROGRESS_JSON,
        controller.PROGRESS_MD,
    ]
    isolated_digests_before = {path: _file_digest(path) for path in isolated_paths}

    state = {
        "iteration": 4,
        "attempt": 26,
        "overall_ready": False,
        "next_prompt": "isolated watch fixture",
        "external_watch": {"watch_interval_seconds": 0},
        "history": [],
    }
    discovery = {
        "status": "NOT_READY",
        "candidate": None,
        "candidate_state": "NOT_CACHED_DOWNLOAD_TOO_SLOW_OR_UNAVAILABLE",
        "candidate_policy": {"candidate_references": [], "excluded_reference_absent": False},
        "local": [],
        "remote": [],
    }
    with patch.object(controller, "current_queue", return_value={"issues": []}), patch.object(
        controller, "observe", return_value=_observations()
    ), patch.object(
        controller, "git_snapshot", return_value={"commit": "a" * 40, "status": "CLEAN_SYNCED"}
    ), patch.object(
        controller, "discover_watch_candidates", return_value=discovery
    ), patch.object(controller, "run_codex_child") as child, patch.object(
        controller, "render_reports", return_value={}
    ), patch.object(
        controller, "persist_watch_report_commit", return_value={"attempted": False}
    ) as report_commit:
        result = controller.watch_external_iteration(
            state,
            evidence_root=temporary_evidence,
            cycle=1,
            execute_codex=True,
            timeout_seconds=30,
            max_download_seconds=900,
            max_estimated_hours=0.5,
        )

    child.assert_not_called()
    report_commit.assert_called_once()
    assert result["status"] == "NOT_READY"
    assert (temporary_evidence / "current_state.json").exists()
    assert {path: _file_digest(path) for path in isolated_paths} == isolated_digests_before
    assert _git(isolated_repository, "rev-parse", "HEAD") == isolated_head
    assert json.loads((temporary_evidence / "current_state.json").read_text())["external_watch"]["cycle"] == 1


def test_all_excluded_candidates_do_not_fall_back_to_priority_display():
    state = {
        "iteration": 4,
        "attempt": 26,
        "overall_ready": False,
        "next_prompt": "stale candidate prompt",
        "external_watch": {
            "watch_interval_seconds": 0,
            "rejected_candidates": [
                "codegemma:2b",
                "deepseek-coder:1.3b",
                "qwen2.5-coder:1.5b",
                "starcoder2:3b",
            ],
            "acquisition_blocked_candidates": ["qwen2.5-coder:3b"],
        },
        "history": [],
    }
    discovery = {
        "status": "NOT_READY",
        "candidate": None,
        "candidate_state": "NOT_CACHED_DOWNLOAD_TOO_SLOW_OR_UNAVAILABLE",
        "candidate_policy": {
            "candidate_references": [],
            "excluded_reference_absent": False,
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
            max_download_seconds=10_800,
            max_estimated_hours=3.0,
        )

    child.assert_not_called()
    assert result["candidate"] is None
    assert state["external_watch"]["candidate"] is None
    assert "qwen2.5-coder:3b" not in state["current_action"]
    assert "NONE (all configured candidates excluded or unavailable)" in state["next_prompt"]
    assert "No eligible coding candidate" in state["current_action"]
    assert state["next_prompt_source_commit"] == _observations()["git"]["application_source_commit"]


def test_watch_recovers_rejection_from_prior_trusted_evidence():
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
        "candidate_policy": {"candidate_references": [], "excluded_reference_absent": False},
        "local": [],
        "remote": [],
    }
    with TemporaryDirectory() as directory:
        prior = Path(directory) / "autonomous-loop" / "watch-external" / "cycle-0047"
        prior.mkdir(parents=True)
        (prior / "result.json").write_text(
            __import__("json").dumps(
                {
                    "candidate": "qwen2.5-coder:1.5b",
                    "trusted_parent_focused_gate": {"run_status": "complete"},
                    "focused_objective_gate": {
                        "exact_eight_record_matrix": True,
                        "admitted": False,
                    },
                }
            ),
            encoding="utf-8",
        )

        def fake_discovery(observed_state: dict, **_kwargs: object) -> dict:
            assert observed_state["external_watch"]["rejected_candidates"] == [
                "qwen2.5-coder:1.5b"
            ]
            return discovery

        with patch.object(controller, "current_queue", return_value={"issues": []}), patch.object(
            controller, "observe", return_value=_observations()
        ), patch.object(
            controller, "discover_watch_candidates", side_effect=fake_discovery
        ), patch.object(controller, "render_reports", return_value={}), patch.object(
            controller, "persist_watch_report_commit", return_value={"attempted": False}
        ):
            result = controller.watch_external_iteration(
                state,
                evidence_root=Path(directory),
                cycle=48,
                execute_codex=False,
                timeout_seconds=30,
                max_download_seconds=900,
                max_estimated_hours=0.5,
            )

        assert result["status"] == "NOT_READY"


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


def test_terminal_issue_queue_selects_persisted_acceptance_work_before_external_watch():
    state = {"acceptance_tasks": controller.default_acceptance_tasks()}
    queue = {"issues": [{"id": "ASTER-006", "status": "BLOCKED_EXTERNAL"}]}
    assert controller.choose_issue(queue) is None
    selected = controller.choose_acceptance_task(state)
    assert selected is not None
    assert selected["id"] == "ASTER-STATE-MERGE-001"
    selected["status"] = "COMPLETE"
    selected = controller.choose_acceptance_task(state)
    assert selected is not None
    assert selected["id"] == "ASTER-RUNTIME-PROVENANCE-001"


def test_acceptance_task_merge_preserves_completed_decision_from_stale_writer():
    disk = {
        "ASTER-STATE-MERGE-001": {
            "id": "ASTER-STATE-MERGE-001",
            "status": "COMPLETE",
            "attempts": 1,
            "updated_at": "2026-09-15T18:00:00+00:00",
            "evidence": ["/evidence/complete"],
        }
    }
    stale = {
        "ASTER-STATE-MERGE-001": {
            "id": "ASTER-STATE-MERGE-001",
            "status": "READY",
            "attempts": 0,
            "updated_at": "2026-09-15T17:59:00+00:00",
            "evidence": [],
        }
    }
    merged = controller._merge_acceptance_tasks(disk, stale)
    assert merged["ASTER-STATE-MERGE-001"]["status"] == "COMPLETE"
    assert merged["ASTER-STATE-MERGE-001"]["evidence"] == ["/evidence/complete"]


def test_acceptance_lane_links_two_source_bound_children_without_queue_items():
    state = {
        "iteration": 4,
        "attempt": 26,
        "overall_ready": False,
        "history": [],
        "acceptance_tasks": controller.default_acceptance_tasks(),
    }
    child_calls = []

    def fake_child(prompt: str, iteration_dir: Path, *, timeout_seconds: int, evidence_root: Path) -> dict:
        task_id = "ASTER-STATE-MERGE-001" if not child_calls else "ASTER-RUNTIME-PROVENANCE-001"
        child_calls.append((task_id, prompt, iteration_dir))
        return {
            "command": ["codex", "exec"],
            "cwd": str(controller.REPOSITORY_ROOT),
            "exit_code": 0,
            "timed_out": False,
            "parsed": controller.parse_result_text(
                "ASTER_LOOP_RESULT\nSTATUS=NOT_READY\nISSUES_REMAINING=0\n"
                f"CURRENT_ISSUE={task_id}\nACTION=review\nVERIFICATION=PARTIAL\n"
                "NEXT_PROMPT_BEGIN\nReview the next current task.\nNEXT_PROMPT_END\n"
            ),
        }

    def fake_parent(task, *, evidence_dir, evidence_root):
        return {
            "timestamp": "2026-09-15T18:00:00+00:00",
            "task_id": task["id"],
            "objective_pass": True,
            "verification": {"observations": _observations()},
            "evidence_dir": str(evidence_dir),
        }

    with TemporaryDirectory() as directory, patch.object(
        controller, "observe", return_value=_observations()
    ), patch.object(controller, "current_queue", return_value={"issues": [{"id": "ASTER-006", "status": "BLOCKED_EXTERNAL"}]}), patch.object(
        controller, "run_codex_child", side_effect=fake_child
    ), patch.object(controller, "verify_acceptance_task", side_effect=fake_parent), patch.object(
        controller, "render_reports", return_value={}
    ), patch.object(controller, "persist_watch_report_commit", return_value={"attempted": False}), patch.object(
        controller, "git_snapshot", return_value=_observations()["git"]
    ):
        first = controller.acceptance_task_iteration(
            state, evidence_root=Path(directory), cycle=100, execute_codex=True, timeout_seconds=30
        )
        second = controller.acceptance_task_iteration(
            state, evidence_root=Path(directory), cycle=101, execute_codex=True, timeout_seconds=30
        )

    assert first["issue"] == "ASTER-STATE-MERGE-001"
    assert second["issue"] == "ASTER-RUNTIME-PROVENANCE-001"
    assert [item[0] for item in child_calls] == [
        "ASTER-STATE-MERGE-001",
        "ASTER-RUNTIME-PROVENANCE-001",
    ]
    assert state["acceptance_tasks"]["ASTER-STATE-MERGE-001"]["status"] == "COMPLETE"
    assert state["acceptance_tasks"]["ASTER-RUNTIME-PROVENANCE-001"]["status"] == "COMPLETE"
    assert state["external_watch"]["cycle"] == 101


def _valid_child(task_id: str) -> dict:
    return {
        "command": ["codex", "exec"],
        "cwd": str(controller.REPOSITORY_ROOT),
        "exit_code": 0,
        "timed_out": False,
        "parsed": controller.parse_result_text(
            "ASTER_LOOP_RESULT\nSTATUS=NOT_READY\nISSUES_REMAINING=0\n"
            f"CURRENT_ISSUE={task_id}\nACTION=review\nVERIFICATION=PARTIAL\n"
            "NEXT_PROMPT_BEGIN\nReview the trusted result.\nNEXT_PROMPT_END\n"
        ),
    }


def _release_state() -> dict:
    state = {
        "iteration": 4,
        "attempt": 26,
        "overall_ready": False,
        "history": [],
        "acceptance_tasks": controller.default_acceptance_tasks(),
    }
    state["acceptance_tasks"]["ASTER-STATE-MERGE-001"]["status"] = "COMPLETE"
    state["acceptance_tasks"]["ASTER-RUNTIME-PROVENANCE-001"]["status"] = "COMPLETE"
    return state


def test_local_acceptance_failure_dispatches_bounded_repair_instead_of_external_completion():
    state = _release_state()
    parent = {
        "task_id": "ASTER-RELEASE-VALIDATION-001",
        "objective_pass": False,
        "reason": "Expo Doctor found a local dependency mismatch",
        "failure_classification": "LOCAL_MOBILE_DEPENDENCY_MISMATCH",
        "verification": {"observations": _observations()},
    }
    with TemporaryDirectory() as directory, patch.object(
        controller, "observe", return_value=_observations()
    ), patch.object(
        controller, "current_queue", return_value={"issues": []}
    ), patch.object(
        controller, "run_codex_child", return_value=_valid_child("ASTER-RELEASE-VALIDATION-001")
    ), patch.object(
        controller, "verify_acceptance_task", return_value=parent
    ), patch.object(
        controller, "render_reports", return_value={}
    ), patch.object(
        controller, "persist_watch_report_commit", return_value={"attempted": False}
    ), patch.object(
        controller, "git_snapshot", return_value=_observations()["git"]
    ):
        result = controller.acceptance_task_iteration(
            state,
            evidence_root=Path(directory),
            cycle=200,
            execute_codex=True,
            timeout_seconds=30,
        )

    release = state["acceptance_tasks"]["ASTER-RELEASE-VALIDATION-001"]
    repairs = [
        task for task in state["acceptance_tasks"].values()
        if isinstance(task, dict) and task.get("repair_kind") == "release_dependency_alignment"
    ]
    assert result["verification"] == "FAIL"
    assert result["objective_pass"] is False
    assert release["status"] == "FAILED"
    assert release["status"] != "COMPLETE_WITH_EXTERNAL"
    assert len(repairs) == 1
    assert repairs[0]["status"] == "READY"
    assert controller.choose_acceptance_task(state)["id"] == repairs[0]["id"]


def test_verified_external_acceptance_failure_is_blocked_without_repair(tmp_path):
    state = _release_state()
    evidence = tmp_path / "external-proof.log"
    evidence.write_text("provider endpoint rejected callback credential\n", encoding="utf-8")
    parent = {
        "task_id": "ASTER-RELEASE-VALIDATION-001",
        "objective_pass": False,
        "reason": "provider endpoint unavailable",
        "failure_classification": "BLOCKED_EXTERNAL",
        "external_evidence": [str(evidence)],
        "unblock_condition": "Provider restores the authenticated endpoint",
        "verification": {"observations": _observations()},
    }
    with TemporaryDirectory() as directory, patch.object(
        controller, "observe", return_value=_observations()
    ), patch.object(
        controller, "current_queue", return_value={"issues": []}
    ), patch.object(
        controller, "run_codex_child", return_value=_valid_child("ASTER-RELEASE-VALIDATION-001")
    ), patch.object(
        controller, "verify_acceptance_task", return_value=parent
    ), patch.object(
        controller, "render_reports", return_value={}
    ), patch.object(
        controller, "persist_watch_report_commit", return_value={"attempted": False}
    ), patch.object(
        controller, "git_snapshot", return_value=_observations()["git"]
    ):
        result = controller.acceptance_task_iteration(
            state,
            evidence_root=Path(directory),
            cycle=201,
            execute_codex=True,
            timeout_seconds=30,
        )

    release = state["acceptance_tasks"]["ASTER-RELEASE-VALIDATION-001"]
    assert controller._verified_external_failure(parent)["classification"] == "BLOCKED_EXTERNAL"
    assert result["verification"] == "BLOCKED"
    assert release["status"] == "BLOCKED_EXTERNAL"
    assert not any(
        isinstance(task, dict) and task.get("parent_task_id") == release["id"]
        for task in state["acceptance_tasks"].values()
    )


def test_automatic_repair_chain_runs_original_failure_then_repair_child(tmp_path):
    state = _release_state()
    child_calls = []
    parent_results = [
        {
            "task_id": "ASTER-RELEASE-VALIDATION-001",
            "objective_pass": False,
            "reason": "local mobile mismatch",
            "failure_classification": "LOCAL_MOBILE_DEPENDENCY_MISMATCH",
            "verification": {"observations": _observations()},
        },
        {
            "task_id": "ASTER-REPAIR-ASTER-RELEASE-VALIDATION-001",
            "objective_pass": True,
            "reason": "repair verified",
            "verification": {"observations": _observations()},
        },
    ]

    def fake_child(prompt, iteration_dir, *, timeout_seconds, evidence_root):
        task_id = (
            "ASTER-RELEASE-VALIDATION-001"
            if not child_calls
            else "ASTER-REPAIR-ASTER-RELEASE-VALIDATION-001"
        )
        child_calls.append(task_id)
        return _valid_child(task_id)

    with patch.object(controller, "observe", return_value=_observations()), patch.object(
        controller, "current_queue", return_value={"issues": []}
    ), patch.object(controller, "run_codex_child", side_effect=fake_child), patch.object(
        controller, "verify_acceptance_task", side_effect=parent_results
    ), patch.object(controller, "render_reports", return_value={}), patch.object(
        controller, "persist_watch_report_commit", return_value={"attempted": False}
    ), patch.object(controller, "git_snapshot", return_value=_observations()["git"]):
        first = controller.acceptance_task_iteration(
            state, evidence_root=tmp_path, cycle=202, execute_codex=True, timeout_seconds=30
        )
        second = controller.acceptance_task_iteration(
            state, evidence_root=tmp_path, cycle=203, execute_codex=True, timeout_seconds=30
        )

    assert first["verification"] == "FAIL"
    assert second["verification"] == "PASS"
    assert child_calls == [
        "ASTER-RELEASE-VALIDATION-001",
        "ASTER-REPAIR-ASTER-RELEASE-VALIDATION-001",
    ]
    assert state["acceptance_tasks"]["ASTER-RELEASE-VALIDATION-001"]["status"] == "PENDING"
    assert state["acceptance_tasks"]["ASTER-REPAIR-ASTER-RELEASE-VALIDATION-001"]["status"] == "COMPLETE"


def test_runtime_repair_uses_trusted_parent_commands_and_current_attestation(tmp_path):
    runtime_evidence = tmp_path / "runtime.json"
    runtime_evidence.write_text("{}", encoding="utf-8")
    observations = _observations()
    observations["runtime"] = {
        "status": "PASS",
        "evidence": str(runtime_evidence),
        "source_matches_current": True,
        "matches": {"source_commit": True, "backend_source_sha256": True, "web_bundle_sha256": True},
    }
    commands = []

    def fake_command(command, cwd, **kwargs):
        commands.append(command)
        return {"command": command, "cwd": str(cwd), "exit_code": 0, "stdout": "", "stderr": ""}

    with patch.object(controller, "command_record", side_effect=fake_command), patch.object(
        controller, "observe", return_value=observations
    ), patch.object(
        controller,
        "capture_current_runtime_attestation",
        return_value={"objective_pass": True},
    ):
        result = controller.verify_acceptance_task(
            {"id": "ASTER-REPAIR-ASTER-RUNTIME-PROVENANCE-001", "repair_kind": "runtime_deployment_repair"},
            evidence_dir=tmp_path / "repair",
            evidence_root=tmp_path,
        )

    assert result["objective_pass"] is True
    assert commands[0][:4] == ["systemctl", "--user", "restart", "work-station-backend.service"]
    assert commands[1][0] == "curl"


def test_failed_runtime_task_recovery_creates_one_persisted_repair_after_restart(tmp_path):
    evidence_dir = tmp_path / "cycle-0874"
    evidence_dir.mkdir()
    state = controller.load_or_create_state(tmp_path / "state")
    failed = state["acceptance_tasks"]["ASTER-RUNTIME-PROVENANCE-001"]
    failed.update(
        {
            "status": "FAILED",
            "attempts": 7,
            "evidence": [str(evidence_dir)],
            "last_result": {
                "cycle": 874,
                "child": {"exit_code": 0, "timed_out": False},
                "parent_verification": {
                    "objective_pass": False,
                    "reason": "runtime reports historical source",
                    "failure_classification": "LOCAL_RUNTIME_PROVENANCE_FAILED",
                    "evidence_dir": str(evidence_dir),
                },
            },
        }
    )
    controller.persist_state(state, tmp_path / "state")
    recovered = controller.load_or_create_state(tmp_path / "state")
    repairs = controller.recover_persisted_acceptance_failure_repairs(recovered)
    controller.persist_state(recovered, tmp_path / "state")
    restarted = controller.load_or_create_state(tmp_path / "state")
    repair_tasks = [
        task for task in restarted["acceptance_tasks"].values()
        if isinstance(task, dict) and task.get("repair_kind") == "runtime_deployment_repair"
    ]
    assert len(repairs) == 1
    assert len(repair_tasks) == 1
    assert repair_tasks[0]["status"] == "READY"
    assert controller.recover_persisted_acceptance_failure_repairs(restarted) == [repair_tasks[0]]


def test_failed_dynamic_repair_reopens_only_within_its_finite_budget():
    state = {"acceptance_tasks": controller.default_acceptance_tasks()}
    # The dynamic record is not in the static defaults, so seed it explicitly.
    state["acceptance_tasks"]["ASTER-REPAIR-TEST"] = {
        "id": "ASTER-REPAIR-TEST",
        "status": "FAILED",
        "repair_kind": "test_repair",
        "attempts": 1,
        "max_attempts": 2,
    }
    controller.acceptance_task_records(state)
    assert state["acceptance_tasks"]["ASTER-REPAIR-TEST"]["status"] == "RETRY"
    state["acceptance_tasks"]["ASTER-REPAIR-TEST"]["attempts"] = 2
    state["acceptance_tasks"]["ASTER-REPAIR-TEST"]["status"] = "FAILED"
    controller.acceptance_task_records(state)
    assert state["acceptance_tasks"]["ASTER-REPAIR-TEST"]["status"] == "FAILED"


def test_duplicate_active_repairs_are_reconciled_without_losing_history():
    state = {"acceptance_tasks": controller.default_acceptance_tasks()}
    state["acceptance_tasks"].update(
        {
            "ASTER-REPAIR-RUNTIME-001": {
                "id": "ASTER-REPAIR-RUNTIME-001",
                "priority": "P0",
                "parent_task_id": "ASTER-RUNTIME-PROVENANCE-001",
                "repair_kind": "runtime_deployment_repair",
                "status": "FAILED",
                "attempts": 1,
                "max_attempts": 2,
                "evidence": ["/evidence/old"],
            },
            "ASTER-REPAIR-RUNTIME-001-002": {
                "id": "ASTER-REPAIR-RUNTIME-001-002",
                "priority": "P0",
                "parent_task_id": "ASTER-RUNTIME-PROVENANCE-001",
                "repair_kind": "runtime_deployment_repair",
                "status": "READY",
                "attempts": 0,
                "max_attempts": 2,
                "evidence": ["/evidence/new"],
            },
        }
    )
    state["acceptance_tasks"]["ASTER-STATE-MERGE-001"]["status"] = "COMPLETE"
    state["acceptance_tasks"]["ASTER-RUNTIME-PROVENANCE-001"]["status"] = "COMPLETE"
    controller.acceptance_task_records(state)
    assert state["acceptance_tasks"]["ASTER-REPAIR-RUNTIME-001"]["status"] == "RETRY"
    assert state["acceptance_tasks"]["ASTER-REPAIR-RUNTIME-001-002"]["status"] == "SUPERSEDED"
    assert controller.choose_acceptance_task(state)["id"] == "ASTER-REPAIR-RUNTIME-001"


def test_runtime_readiness_fix_grants_exactly_one_new_bounded_attempt():
    state = {"acceptance_tasks": controller.default_acceptance_tasks()}
    state["acceptance_tasks"].update(
        {
            "ASTER-REPAIR-RUNTIME-001": {
                "id": "ASTER-REPAIR-RUNTIME-001",
                "parent_task_id": "ASTER-RUNTIME-PROVENANCE-001",
                "repair_kind": "runtime_deployment_repair",
                "status": "RETRY",
                "attempts": 2,
                "max_attempts": 2,
                "last_result": {
                    "parent_verification": {
                        "failure_classification": "LOCAL_RUNTIME_HEALTH_FAILED"
                    }
                },
            }
        }
    )
    controller.acceptance_task_records(state)
    task = state["acceptance_tasks"]["ASTER-REPAIR-RUNTIME-001"]
    assert task["max_attempts"] == 3
    assert task["readiness_retry_granted"] is True
    controller.acceptance_task_records(state)
    assert task["max_attempts"] == 3


def test_completed_old_repair_does_not_undo_new_source_revalidation():
    state = {"acceptance_tasks": controller.default_acceptance_tasks()}
    runtime = state["acceptance_tasks"]["ASTER-RUNTIME-PROVENANCE-001"]
    runtime.update(
        {
            "status": "COMPLETE",
            "validated_source_commit": "a" * 40,
            "repair_resolution": {
                "repair_task_id": "ASTER-REPAIR-RUNTIME-001",
                "evidence": ["/evidence/old-runtime"],
            },
        }
    )
    state["acceptance_tasks"]["ASTER-REPAIR-RUNTIME-001"] = {
        "id": "ASTER-REPAIR-RUNTIME-001",
        "parent_task_id": "ASTER-RUNTIME-PROVENANCE-001",
        "repair_kind": "runtime_deployment_repair",
        "status": "COMPLETE",
        "attempts": 1,
        "max_attempts": 2,
    }
    controller.acceptance_task_records(state)
    assert controller.refresh_source_bound_acceptance_tasks(state, "b" * 40) is True
    assert runtime["status"] == "RETRY"
    controller.acceptance_task_records(state)
    assert runtime["status"] == "RETRY"


def test_source_revalidation_supersedes_failed_repair_from_old_source():
    state = {"acceptance_tasks": controller.default_acceptance_tasks()}
    state["acceptance_tasks"]["ASTER-STATE-MERGE-001"]["status"] = "COMPLETE"
    runtime = state["acceptance_tasks"]["ASTER-RUNTIME-PROVENANCE-001"]
    runtime.update(
        {
            "status": "FAILED",
            "validated_source_commit": "a" * 40,
            "last_result": {"parent_verification": {"objective_pass": False}},
        }
    )
    repair = {
        "id": "ASTER-REPAIR-RUNTIME-OLD-SOURCE",
        "parent_task_id": "ASTER-RUNTIME-PROVENANCE-001",
        "repair_kind": "runtime_deployment_repair",
        "status": "FAILED",
        "attempts": 2,
        "max_attempts": 2,
        "validated_source_commit": "a" * 40,
        "evidence": ["/evidence/old-repair"],
    }
    state["acceptance_tasks"][repair["id"]] = repair

    assert controller.refresh_source_bound_acceptance_tasks(state, "b" * 40) is True
    assert runtime["status"] == "RETRY"
    assert repair["status"] == "SUPERSEDED"
    assert controller.choose_acceptance_task(state)["id"] == runtime["id"]


def test_release_repair_requires_proof_and_runs_bounded_workspace_commands(tmp_path):
    evidence_dir = tmp_path / "failed-release"
    evidence_dir.mkdir()
    (evidence_dir / "command.stdout.log").write_text(
        "expo                ~57.0.23  57.0.22\n"
        "expo-image-picker   ~57.0.18  57.0.17\n"
        "expo-notifications  ~57.0.19  57.0.18\n"
        "3 packages out of date.\n",
        encoding="utf-8",
    )
    commands = []

    def fake_command(command, cwd, **kwargs):
        commands.append(command)
        return {"command": command, "cwd": str(cwd), "exit_code": 0, "stdout": "", "stderr": ""}

    with patch.object(controller, "command_record", side_effect=fake_command), patch.object(
        controller,
        "resolve_node_runtime",
        return_value={"environment": {"PATH": os.environ.get("PATH", "")}, "node_version": "24.19.0"},
    ), patch.object(
        controller,
        "safe_commit_parent_repair",
        return_value={"committed": True, "push": {"exit_code": 0}},
    ):
        result = controller.verify_acceptance_task(
            {
                "id": "ASTER-REPAIR-ASTER-RELEASE-VALIDATION-001",
                "repair_kind": "release_dependency_alignment",
                "evidence": [str(evidence_dir)],
            },
            evidence_dir=tmp_path / "repair",
            evidence_root=tmp_path,
        )

    assert result["objective_pass"] is True
    assert commands[0][:4] == ["npm", "install", "--workspace", "@work-station/mobile"]
    assert commands[1] == [str(controller.REPOSITORY_ROOT / "scripts/mobile_check.sh"), "--skip-native-android"]


def test_runtime_provenance_parent_check_reads_nested_runtime_matches():
    observations = _observations()
    observations["runtime"] = {
        "status": "PASS",
        "evidence": str(Path(__file__).resolve()),
        "source_matches_current": True,
        "matches": {
            "source_commit": True,
            "backend_source_sha256": True,
            "web_bundle_sha256": True,
        },
    }
    with TemporaryDirectory() as directory, patch.object(
        controller, "observe", return_value=observations
    ), patch.object(
        controller,
        "capture_current_runtime_attestation",
        return_value={"objective_pass": True},
    ):
        result = controller.verify_acceptance_task(
            {
                "id": "ASTER-RUNTIME-PROVENANCE-001",
                "status": "RETRY",
                "attempts": 1,
            },
            evidence_dir=Path(directory),
            evidence_root=Path(directory),
        )
    assert result["objective_pass"] is True


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
