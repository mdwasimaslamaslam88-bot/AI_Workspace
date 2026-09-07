import json
import os
from pathlib import Path
import subprocess


def _run_server(requests, *, mode="read_only"):
    script = Path(__file__).resolve().parents[3] / "scripts/ai_os_mcp_server.py"
    environment = {
        "PATH": os.environ["PATH"],
        "AI_OS_DEX_CALLBACK_MODE": mode,
    }
    completed = subprocess.run(
        ["python3", str(script)],
        input="".join(json.dumps(item) + "\n" for item in requests),
        text=True,
        capture_output=True,
        timeout=5,
        check=True,
        env=environment,
    )
    return [json.loads(line) for line in completed.stdout.splitlines()]


def test_mcp_server_advertises_only_read_only_analysis_by_default():
    responses = _run_server(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
    )

    assert responses[0]["result"]["serverInfo"]["name"] == "ai-os-dex-bridge"
    assert [tool["name"] for tool in responses[1]["result"]["tools"]] == [
        "ai_os_analyze"
    ]


def test_mcp_server_exposes_one_scoped_action_contract_only_in_write_mode():
    responses = _run_server(
        [{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}],
        mode="workspace_write",
    )
    tool = responses[0]["result"]["tools"][0]

    assert tool["name"] == "ai_os_execute_and_review"
    assert tool["inputSchema"]["required"] == ["question", "path", "content"]
    assert "command" not in json.dumps(tool).lower()


def test_mcp_server_rejects_unknown_tools_without_leaking_input():
    secret_marker = "never-print-this-marker"
    responses = _run_server(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "shell", "arguments": {"value": secret_marker}},
            }
        ]
    )

    encoded = json.dumps(responses)
    assert responses[0]["result"]["isError"] is True
    assert secret_marker not in encoded
