#!/usr/bin/env bash
set -euo pipefail

gate="${1:-}"
repository_root="$(pwd -P)"
[[ -d "${repository_root}/.git" ]]
[[ -x "${repository_root}/backend/.venv/bin/python" ]]
backend_python="${repository_root}/backend/.venv/bin/python"

case "${gate}" in
  source)
    git diff --check
    [[ -z "$(git status --porcelain=v1 --untracked-files=no)" ]]
    ;;
  backend)
    (
      cd backend
      .venv/bin/python -m pytest -q
      .venv/bin/python -m alembic check
      .venv/bin/python -m compileall -q app scripts tests
    )
    ;;
  database)
    ./scripts/postgres_integration_check.sh
    ;;
  web)
    npm run test:web
    npm run typecheck --workspace @work-station/shared
    npm run typecheck --workspace @work-station/web
    npm run lint --workspace @work-station/web
    npm run build:web
    ;;
  mobile)
    ./scripts/mobile_check.sh --skip-native-android
    ;;
  desktop)
    ./scripts/desktop_check.sh --skip-launch
    ;;
  browser_e2e)
    ./scripts/postgres_integration_check.sh --browser-only
    ;;
  rag_memory)
    (
      cd backend
      .venv/bin/python -m pytest -q \
        tests/documents tests/memory tests/test_documents_api.py \
        tests/test_memories_api.py tests/services/test_document_service.py \
        tests/services/test_memory.py tests/test_ollama_embedding_runtime.py
    )
    ;;
  vision_image_voice)
    (
      cd backend
      .venv/bin/python -m pytest -q \
        tests/runtimes tests/test_image_config.py tests/test_image_model_registry.py \
        tests/test_images_api.py tests/test_real_voice_smoke.py \
        tests/test_speech_config.py tests/test_voice_api.py \
        tests/services/test_image.py tests/services/test_vision_input.py \
        tests/services/test_voice.py
    )
    ;;
  tools_workflows_agents)
    (
      cd backend
      .venv/bin/python -m pytest -q \
        tests/agent_os tests/test_agent_os_api.py tests/test_tools_api.py \
        tests/test_workflows_api.py tests/services/test_tool.py \
        tests/services/test_workflow.py
    )
    ;;
  routing_admission_hardware)
    (
      cd backend
      .venv/bin/python -m pytest -q \
        tests/test_current_hardware_model_discovery.py \
        tests/test_current_hardware_vision_discovery.py \
        tests/test_future_models.py tests/test_hardware_capabilities.py \
        tests/test_hardware_planner.py tests/test_model_admission.py \
        tests/test_model_catalog.py tests/test_task_model_router.py
      .venv/bin/python -m scripts.hardware_upgrade_acceptance
    )
    ;;
  api_fallback)
    (
      cd backend
      .venv/bin/python -m pytest -q \
        tests/external_ai tests/test_external_ai_api.py \
        tests/agent_os/test_orchestrator.py
    )
    ;;
  self_update)
    (
      cd backend
      .venv/bin/python -m pytest -q \
        tests/maintenance tests/test_self_update_api.py
    )
    ;;
  security)
    ./scripts/security_audit.sh
    ;;
  performance)
    (
      cd backend
      .venv/bin/python -m pytest -q \
        tests/test_massive_ai_benchmark.py tests/test_model_candidate_benchmark.py \
        tests/services/test_generation_admission.py
    )
    ;;
  rollback)
    (
      cd backend
      .venv/bin/python -m pytest -q \
        tests/maintenance/test_self_update.py tests/test_backup_tool.py
    )
    ;;
  release)
    ./scripts/release_check.sh
    ;;
  *)
    echo "Unknown mandatory update gate." >&2
    exit 2
    ;;
esac

printf 'update gate passed: %s\n' "${gate}"
