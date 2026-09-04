import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPOSITORY_ROOT / "reports" / "activation-baseline.json"
FEATURE_REPORT_PATH = REPOSITORY_ROOT / "reports" / "feature-registry-report.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_activation_baseline_remains_a_valid_historical_feature_authority():
    baseline = _read_json(BASELINE_PATH)
    feature_report = _read_json(FEATURE_REPORT_PATH)

    assert baseline["schema_version"] == 1
    assert baseline["activation_phase"] == "A"
    assert baseline["status"] == "PASS"
    assert baseline["repository"]["branch"] == "main"
    assert baseline["repository"]["upstream"] == "origin/main"
    assert baseline["repository"]["ahead"] == 0
    assert baseline["repository"]["behind"] == 0
    assert len(baseline["repository"]["source_commit"]) == 40

    authority = baseline["feature_registry"]
    assert len(authority["sha256"]) == 64
    assert authority["total"] == 245
    assert feature_report["total"] >= authority["total"]
    assert feature_report["statuses"]["implemented"] >= authority["statuses"]["implemented"]
    for status in ("runtime_dependent", "external_dependency"):
        assert feature_report["statuses"][status] == authority["statuses"][status]
    assert feature_report["statuses"]["planned"] <= authority["statuses"]["planned"]
    assert feature_report["validation"] == authority["validation"]
    assert sum(authority["statuses"].values()) == authority["total"]


def test_activation_baseline_records_only_verified_or_bounded_state():
    baseline = _read_json(BASELINE_PATH)

    assert baseline["database"]["current_revision"] == "0017_creative_experiences"
    assert baseline["database"]["migration_drift"] is False
    assert baseline["database"]["pre_migration_backup"]["integrity"] == "PASS"
    assert baseline["service_state"]["backend"]["binding"] == "127.0.0.1:8000"
    assert baseline["service_state"]["backend"]["readiness"] == "PASS"
    assert set(baseline["service_state"]["dependencies"].values()) == {"ready"}

    connectors = baseline["connector_state"]
    assert connectors["real_loopback_e2e"] == "PASS"
    assert connectors["configured"] == connectors["active"] == connectors["healthy"] == 0
    assert connectors["external_provider_state"] == "OWNER_ACTION_REQUIRED"

    security = baseline["security_state"]
    assert security["status"] == "PASS"
    assert security["critical_findings"] == security["high_findings"] == 0
    assert security["moderate_findings"] == 14
    assert baseline["external_boundaries"]["feature_count"] == 39

    verification = baseline["verification"]
    assert verification["backend_regression"] == {
        "passed": 2929,
        "skipped": 48,
        "failed": 0,
    }
    assert verification["focused_tests"] == {"passed": 65, "failed": 0}
    assert verification["postgresql_integration"] == {"passed": 48, "failed": 0}
    assert verification["runtime_sections"]["passed"] == 12
    assert verification["runtime_sections"]["failed"] == 0
    assert verification["release_artifacts"] == {"passed": 8, "failed": 0}


def test_activation_baseline_release_and_model_hashes_are_well_formed():
    baseline = _read_json(BASELINE_PATH)

    artifacts = baseline["platform_state"]["artifacts"]
    assert len(artifacts) == 8
    assert len({artifact["path"] for artifact in artifacts}) == 8
    assert len({artifact["sha256"] for artifact in artifacts}) == 8

    image_artifacts = baseline["model_state"]["image_runtime"]["artifacts"]
    assert len(image_artifacts) == 3
    assert baseline["model_state"]["image_runtime"]["integrity"] == "PASS"

    for digest in [
        *(artifact["sha256"] for artifact in artifacts),
        *(artifact["sha256"] for artifact in image_artifacts),
    ]:
        assert len(digest) == 64
        assert all(character in "0123456789abcdef" for character in digest)

    routes = baseline["model_state"]["production_routes"]
    assert routes["code_generation"] == "gemma4:12b-it-q4_K_M"
    assert routes["image"] == "flux2-klein-base-4b-fp8"
    assert routes["vision"] == "qwen2.5vl:7b"
