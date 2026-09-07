#!/usr/bin/env python3
"""Short-lived STDIO MCP bridge from a bounded DEX task to AI OS."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys


MAX_MESSAGE_BYTES = 16_384


def _response(request_id, result=None, error=None):
    value = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        value["error"] = error
    else:
        value["result"] = result
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _call_ai_os(arguments, *, workspace_write):
    expected = {"question", "path", "content"} if workspace_write else {"question"}
    if not isinstance(arguments, dict) or set(arguments) != expected:
        raise ValueError("invalid tool arguments")
    question = arguments["question"]
    if not isinstance(question, str) or not question.strip() or len(question) > 8000:
        raise ValueError("invalid tool question")
    socket_text = os.environ.get("AI_OS_DEX_SOCKET_PATH", "")
    token = os.environ.get("AI_OS_DEX_CAPABILITY_TOKEN", "")
    task_id = os.environ.get("AI_OS_DEX_TASK_ID", "")
    correlation_id = os.environ.get("AI_OS_DEX_CORRELATION_ID", "")
    owner_id = os.environ.get("AI_OS_DEX_OWNER_ID", "")
    socket_path = Path(socket_text)
    if (
        not socket_path.is_absolute()
        or not token
        or not task_id
        or not correlation_id
        or not owner_id
    ):
        raise RuntimeError("AI OS callback is unavailable")
    payload = json.dumps(
        {
            "token": token,
            "task_id": task_id,
            "correlation_id": correlation_id,
            "owner_id": owner_id,
            "question": question.strip(),
            "operation": (
                {
                    "tool": "filesystem.write",
                    "arguments": {
                        "path": arguments["path"],
                        "content": arguments["content"],
                        "root_index": 0,
                    },
                }
                if workspace_write
                else None
            ),
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("callback request is too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(65)
        client.connect(str(socket_path))
        client.sendall(payload)
        chunks = bytearray()
        while b"\n" not in chunks:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > MAX_MESSAGE_BYTES:
                raise RuntimeError("callback response is too large")
    response = json.loads(bytes(chunks).split(b"\n", 1)[0])
    if not isinstance(response, dict):
        raise RuntimeError("callback response is invalid")
    return response


def main():
    callback_mode = os.environ.get("AI_OS_DEX_CALLBACK_MODE", "read_only")
    workspace_write = callback_mode == "workspace_write"
    tool_name = "ai_os_execute_and_review" if workspace_write else "ai_os_analyze"
    for line in sys.stdin:
        if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue
        method = request.get("method")
        request_id = request.get("id")
        if request_id is None:
            continue
        if method == "initialize":
            _response(
                request_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ai-os-dex-bridge", "version": "1.0.0"},
                },
            )
        elif method == "ping":
            _response(request_id, {})
        elif method == "tools/list":
            _response(
                request_id,
                {
                    "tools": [
                        {
                            "name": tool_name,
                            "description": (
                                "Ask AI OS for an independent local-only review"
                                + (
                                    " and execute one owner-scoped filesystem.write through the existing ToolService."
                                    if workspace_write
                                    else ". This tool cannot mutate local state."
                                )
                            ),
                            "inputSchema": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": (
                                    ["question", "path", "content"]
                                    if workspace_write
                                    else ["question"]
                                ),
                                "properties": {
                                    "question": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 8000,
                                    },
                                    **(
                                        {
                                            "path": {
                                                "type": "string",
                                                "minLength": 1,
                                                "maxLength": 512,
                                            },
                                            "content": {
                                                "type": "string",
                                                "maxLength": 8000,
                                            },
                                        }
                                        if workspace_write
                                        else {}
                                    ),
                                },
                            },
                        }
                    ]
                },
            )
        elif method == "tools/call":
            params = request.get("params")
            try:
                if not isinstance(params, dict) or params.get("name") != tool_name:
                    raise ValueError("unknown tool")
                result = _call_ai_os(
                    params.get("arguments"), workspace_write=workspace_write
                )
                _response(
                    request_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, separators=(",", ":")),
                            }
                        ],
                        "structuredContent": result,
                        "isError": result.get("status") != "VERIFIED",
                    },
                )
            except Exception:
                _response(
                    request_id,
                    {
                        "content": [
                            {"type": "text", "text": "AI OS callback was rejected."}
                        ],
                        "isError": True,
                    },
                )
        else:
            _response(
                request_id,
                error={"code": -32601, "message": "Method not found"},
            )


if __name__ == "__main__":
    main()
