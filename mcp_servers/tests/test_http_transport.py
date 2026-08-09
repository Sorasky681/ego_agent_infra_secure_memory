from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _post(port: int, payload: dict[str, Any], session_id: str | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    connection.request("POST", "/mcp", body=json.dumps(payload), headers=headers)
    response = connection.getresponse()
    body = response.read()
    result = response.status, response.getheader("Mcp-Session-Id"), json.loads(body)
    connection.close()
    return result


def test_streamable_http_initialize_and_tools_list(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("bounded repo\n", encoding="utf-8")
    port = _free_loopback_port()
    environment = {
        **os.environ,
        "EGO_MCP_REPO_ROOT": str(tmp_path),
        "EGO_MCP_TRANSPORT": "streamable-http",
        "EGO_MCP_HOST": "127.0.0.1",
        "EGO_MCP_PORT": str(port),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "egoagentos_mcp.repo_server"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "egoagentos-test", "version": "0.1.0"},
        },
    }
    try:
        response = None
        for _ in range(60):
            try:
                response = _post(port, initialize)
                break
            except (ConnectionError, OSError):
                if process.poll() is not None:
                    break
                time.sleep(0.05)
        assert response is not None and process.poll() is None
        status, session_id, payload = response
        assert status == 200
        assert session_id
        assert payload["result"]["serverInfo"]["name"] == "egoagentos-repo"

        status, _, payload = _post(
            port,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            session_id,
        )
        assert status == 200
        assert [tool["name"] for tool in payload["result"]["tools"]] == [
            "repo_snapshot",
            "repo_read_files",
        ]
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
