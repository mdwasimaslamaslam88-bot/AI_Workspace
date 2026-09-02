from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from scripts.disposable_database_environment import load_backend_environment


PROJECT_ROOT = Path(__file__).parents[1]
RUNTIME_SMOKE_MODULES = (
    "scripts.real_vision_smoke",
    "scripts.real_rag_smoke",
    "scripts.real_memory_smoke",
    "scripts.real_image_smoke",
    "scripts.real_voice_smoke",
    "scripts.real_agent_os_smoke",
    "scripts.real_connector_smoke",
    "scripts.real_marketing_smoke",
    "scripts.real_finance_smoke",
    "scripts.real_learning_smoke",
    "scripts.real_creative_smoke",
    "scripts.real_tools_smoke",
    "scripts.real_workflow_smoke",
)


def run_runtime_modules(
    environment: Mapping[str, str],
    *,
    modules: Sequence[str] = RUNTIME_SMOKE_MODULES,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    for module in modules:
        runner(
            [sys.executable, "-m", module],
            cwd=PROJECT_ROOT,
            env=dict(environment),
            check=True,
        )


def main() -> None:
    run_runtime_modules(load_backend_environment(PROJECT_ROOT))
    print(
        "real runtime E2E: vision, RAG, memory, image, voice, Agent OS, "
        "connectors, marketing, finance, learning, creative experiences, tools, and workflows passed"
    )


if __name__ == "__main__":
    main()
