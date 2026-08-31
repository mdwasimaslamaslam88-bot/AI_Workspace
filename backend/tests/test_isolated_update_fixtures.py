import json
from threading import Thread
from urllib.request import urlopen

from scripts.isolated_update_nvidia_smi import output_for
from scripts.isolated_update_ollama import (
    MODEL_REFERENCE,
    build_server,
    response_for,
)


def test_isolated_hardware_fixture_supports_only_detector_calls():
    assert output_for(()) == "| CUDA Version: 0.0 |\n"
    assert output_for(
        (
            "--query-gpu=name,memory.total,memory.free,compute_cap,driver_version",
            "--format=csv,noheader,nounits",
        )
    ) == "WORK STATION isolated validation GPU, 12288, 12288, 8.6, 0.0\n"
    assert output_for(("--unsupported",)) is None


def test_isolated_ollama_fixture_exposes_bounded_inventory_and_details():
    inventory_status, inventory = response_for("GET", "/api/tags")
    detail_status, details = response_for(
        "POST",
        "/api/show",
        {"model": MODEL_REFERENCE},
    )

    assert inventory_status == detail_status == 200
    assert inventory == {
        "models": [
            {
                "model": MODEL_REFERENCE,
                "size": 64 * 1024**2,
                "details": {
                    "family": "workstation-validation",
                    "parameter_size": "0.1B",
                    "quantization_level": "fixture",
                },
            }
        ]
    }
    assert details["capabilities"] == ["completion"]


def test_isolated_ollama_fixture_rejects_unknown_or_malformed_requests():
    assert response_for("GET", "/unknown")[0] == 404
    assert response_for("POST", "/api/show", {"model": "unknown"})[0] == 400
    assert response_for("POST", "/api/chat", {"model": MODEL_REFERENCE})[0] == 400


def test_isolated_ollama_fixture_returns_nonempty_generation():
    status, payload = response_for(
        "POST",
        "/api/chat",
        {
            "model": MODEL_REFERENCE,
            "messages": [{"role": "user", "content": "release smoke"}],
            "stream": False,
        },
    )

    assert status == 200
    assert payload["done"] is True
    assert payload["message"]["content"]


def test_isolated_ollama_server_binds_loopback_and_emits_bounded_json():
    server = build_server(0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - fixed loopback fixture URL
            f"http://127.0.0.1:{server.server_port}/api/tags",
            timeout=2,
        ) as response:
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert json.load(response)["models"][0]["model"] == MODEL_REFERENCE
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
