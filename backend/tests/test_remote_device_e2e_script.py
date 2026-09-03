import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "remote_device_e2e.py"
SPEC = importlib.util.spec_from_file_location("remote_device_e2e", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
remote_device_e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remote_device_e2e)


def _denial(**changes):
    value = {
        "success": False,
        "error": {
            "code": "HTTP_ERROR",
            "message": "Invalid authentication credentials",
        },
        "path": "/api/v1/users/me",
        "timestamp": "2026-09-03T08:00:00+00:00",
    }
    value.update(changes)
    return value


def test_remote_smoke_accepts_only_the_uniform_credential_free_denial():
    assert remote_device_e2e._is_uniform_authentication_denial(
        401,
        _denial(),
        "/api/v1/users/me",
    )
    assert not remote_device_e2e._is_uniform_authentication_denial(
        401,
        _denial(token="leaked"),
        "/api/v1/users/me",
    )
    assert not remote_device_e2e._is_uniform_authentication_denial(
        403,
        _denial(),
        "/api/v1/users/me",
    )
    assert not remote_device_e2e._is_uniform_authentication_denial(
        401,
        _denial(path="/api/v1/users/foreign"),
        "/api/v1/users/me",
    )


def test_remote_smoke_requires_explicit_production_confirmation(monkeypatch, capsys):
    monkeypatch.delenv(
        remote_device_e2e.ACTIVATION_CONFIRMATION,
        raising=False,
    )

    assert remote_device_e2e.main() == 1
    assert capsys.readouterr().err.strip() == (
        "set WORK_STATION_ALLOW_PRODUCTION_REMOTE_SMOKE=YES to permit the "
        "temporary production smoke"
    )
