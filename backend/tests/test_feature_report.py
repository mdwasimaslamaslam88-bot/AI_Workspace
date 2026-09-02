import importlib.util
from pathlib import Path
from types import ModuleType


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT_SCRIPT = REPOSITORY_ROOT / "scripts" / "generate_feature_report.py"


def _load_report_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("feature_report", REPORT_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_feature_implementation_matrix_is_complete_and_prioritized():
    module = _load_report_module()
    registry = module._payload()
    matrix = module._matrix_payload(registry)

    assert matrix["registry_sha256"] == registry["registry_sha256"]
    assert matrix["total"] == registry["total"] == 245
    assert len(matrix["items"]) == registry["total"]
    assert len({item["id"] for item in matrix["items"]}) == registry["total"]
    assert matrix["summary"] == {
        "missing_ui_paths": 0,
        "missing_backend_contracts": 0,
        "missing_coverage_records": 0,
        "broken_registry_wiring": 0,
        "planned_gaps": 5,
        "external_boundaries": 39,
        "runtime_gates": 14,
    }
    assert [gap["priority"] for gap in matrix["gaps"]] == sorted(
        (gap["priority"] for gap in matrix["gaps"]),
        key={"P1": 1, "P2": 2, "P3": 3}.__getitem__,
    )


def test_feature_matrix_keeps_non_ready_capabilities_disabled():
    module = _load_report_module()
    matrix = module._matrix_payload(module._payload())

    for item in matrix["items"]:
        if item["status"] == "external_dependency":
            assert item["ui"]["state"] == "disabled_until_authorized"
            assert item["backend"]["state"] == "external_boundary"
            assert item["dependencies"]
        elif item["status"] == "planned":
            assert item["ui"]["state"] == "disabled_documented_gap"
            assert item["backend"]["state"] == "planned_contract_only"
        elif item["status"] == "runtime_dependent":
            assert item["ui"]["state"] == "runtime_gated"
            assert item["runtime_state"] == "requires_authenticated_health_probe"
