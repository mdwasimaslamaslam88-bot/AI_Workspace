"""Loopback-only Ollama protocol fixture for isolated browser release gates.

This process is launched only by ``postgres_integration_check.sh`` when the
candidate is already inside the no-network self-update sandbox.  It is test
evidence, not a production model runtime or an AI-quality benchmark input.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any


MODEL_REFERENCE = "work-station-update-smoke:latest"
MAX_REQUEST_BYTES = 1_048_576
_GENERATION_TEXT = "The isolated browser release smoke test is ready."


def response_for(
    method: str,
    path: str,
    payload: Any = None,
) -> tuple[int, dict[str, Any]]:
    """Return the deliberately tiny supported protocol surface."""

    if method == "GET" and path == "/api/tags":
        return (
            200,
            {
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
            },
        )
    if method != "POST" or not isinstance(payload, dict):
        return 404, {"error": "unsupported isolated validation request"}
    if path == "/api/show" and payload == {"model": MODEL_REFERENCE}:
        return (
            200,
            {
                "capabilities": ["completion"],
                "model_info": {
                    "general.parameter_count": 100_000_000,
                    "workstation.context_length": 8_192,
                },
            },
        )
    if path == "/api/chat" and _valid_chat_request(payload):
        return (
            200,
            {
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": _GENERATION_TEXT,
                },
            },
        )
    return 400, {"error": "invalid isolated validation request"}


def _valid_chat_request(payload: dict[str, Any]) -> bool:
    messages = payload.get("messages")
    return bool(
        payload.get("model") == MODEL_REFERENCE
        and payload.get("stream") is False
        and isinstance(messages, list)
        and messages
        and all(
            isinstance(message, dict)
            and message.get("role") in {"system", "user", "assistant"}
            and isinstance(message.get("content"), str)
            for message in messages
        )
    )


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WorkStationIsolatedFixture/1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._reply(*response_for("GET", self.path))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        payload = self._read_json()
        if payload is None:
            self._reply(400, {"error": "invalid isolated validation request"})
            return
        self._reply(*response_for("POST", self.path, payload))

    def log_message(self, _format: str, *_arguments: object) -> None:
        return

    def _read_json(self) -> Any | None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isdecimal():
            return None
        length = int(raw_length)
        if length < 1 or length > MAX_REQUEST_BYTES:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            return None

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def build_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.daemon_threads = True
    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    if not 1 <= arguments.port <= 65_535:
        parser.error("port must be between 1 and 65535")
    with build_server(arguments.port) as server:
        server.serve_forever(poll_interval=0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
