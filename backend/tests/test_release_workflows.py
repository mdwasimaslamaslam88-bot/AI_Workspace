from pathlib import Path
import subprocess

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_macos_release_workflow_uses_a_bounded_target_host_contract():
    workflow_path = REPOSITORY_ROOT / ".github/workflows/macos-release.yml"
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["build-macos"]
    assert job["runs-on"] == "macos-latest"
    assert job["timeout-minutes"] == "60"

    commands = "\n".join(step.get("run", "") for step in job["steps"])
    for required in (
        "npm ci",
        "npm run test --workspace @work-station/web",
        "cargo test --locked",
        "npm run build --workspace @work-station/desktop",
        "validate_macos_artifact.sh",
    ):
        assert required in commands
    assert "secrets." not in workflow_path.read_text(encoding="utf-8")


def test_macos_artifact_validator_is_syntax_valid_and_checks_release_boundaries():
    validator = REPOSITORY_ROOT / "scripts/validate_macos_artifact.sh"
    subprocess.run(("/usr/bin/bash", "-n", str(validator)), check=True)
    source = validator.read_text(encoding="utf-8")

    for required in (
        "com.workstation.personalai",
        "hdiutil verify",
        "codesign --verify --deep --strict",
        "USER_PROVISIONING_TOKEN_DIGEST",
        "/Users/runner/|/home/",
        "kill -0",
        "shasum -a 256",
    ):
        assert required in source
    assert "|/tmp/" not in source
