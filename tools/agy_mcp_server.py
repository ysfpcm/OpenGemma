"""Small stdio MCP bridge for delegating implementation tasks to Antigravity.

The bridge intentionally exposes one narrow tool and never enables Antigravity's
dangerous permission bypass. Authentication is handled by the local `agy`
keychain session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


AGY = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
ALLOWED_ROOT = Path.home() / "Documents" / "Codex"


def reply(request_id, result=None, error=None):
    response = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


def run_agy(prompt: str, workspace: str) -> str:
    project = Path(workspace).expanduser().resolve()
    root = ALLOWED_ROOT.resolve()
    if not project.is_dir() or root not in project.parents and project != root:
        raise ValueError(f"workspace must be inside {root}")
    if not AGY.is_file():
        raise FileNotFoundError(f"Antigravity CLI not found at {AGY}")

    completed = subprocess.run(
        [str(AGY), "--print", "--output-format", "json", "--project", str(project), prompt],
        cwd=str(project),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode:
        raise RuntimeError(output or f"agy exited with code {completed.returncode}")
    return output


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                reply(
                    request_id,
                    {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "antigravity-bridge", "version": "0.1.0"},
                    },
                )
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                reply(
                    request_id,
                    {
                        "tools": [
                            {
                                "name": "agy_build",
                                "description": "Delegate an implementation task to Antigravity CLI using the local authenticated account.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "prompt": {"type": "string"},
                                        "workspace": {"type": "string"},
                                    },
                                    "required": ["prompt", "workspace"],
                                },
                            }
                        ]
                    },
                )
            elif method == "tools/call":
                params = request.get("params", {})
                if params.get("name") != "agy_build":
                    reply(request_id, error={"code": -32601, "message": "Unknown tool"})
                    continue
                args = params.get("arguments", {})
                result = run_agy(str(args.get("prompt", "")), str(args.get("workspace", "")))
                reply(request_id, {"content": [{"type": "text", "text": result}]})
            else:
                reply(request_id, error={"code": -32601, "message": f"Unknown method: {method}"})
        except Exception as exc:  # MCP must remain alive after a failed tool call.
            reply(request.get("id") if isinstance(request, dict) else None, error={"code": -32603, "message": str(exc)})


if __name__ == "__main__":
    main()
