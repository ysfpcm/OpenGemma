"""Minimal JSON-RPC stdio client for Codex app-server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Optional


class CodexProtocolError(RuntimeError):
    pass


def resolve_executable(executable: str) -> str:
    """Resolve a directly executable Codex binary, including on Windows."""
    if os.name == "nt" and Path(executable).suffix.lower() not in {".exe", ".com"}:
        shim = shutil.which(f"{executable}.cmd")
        if shim:
            return shim
    return shutil.which(executable) or executable


def executable_command(executable: str, *arguments: str) -> list[str]:
    """Build an argv that can launch npm command shims without a shell flag."""
    resolved = resolve_executable(executable)
    if os.name == "nt" and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
        command = subprocess.list2cmdline([resolved, *arguments])
        return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", command]
    return [resolved, *arguments]


class CodexAppServerClient:
    def __init__(
        self,
        executable: str = "codex",
        *,
        on_notification: Optional[Callable] = None,
        on_request: Optional[Callable] = None,
        on_exit: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.executable = resolve_executable(executable)
        self.on_notification = on_notification or (lambda method, params: None)
        # Server requests are denied unless the supervisor explicitly installs
        # a broker.  This preserves the Phase 1 fail-closed default for any
        # client used outside the Phase 2 supervisor.
        self.on_request = on_request
        self.on_exit = on_exit or (lambda code: None)
        self._process: Optional[subprocess.Popen[str]] = None
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._next_id = 1
        self.denied_requests: list[str] = []
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> dict[str, Any]:
        if self.running:
            raise CodexProtocolError("app-server already running")
        self._stopping = False
        # Do not inherit desktop MCP bridges. They are unrelated to an
        # Ophanim mission and can delay a mission launch while attempting to
        # attach to the desktop app.
        command = executable_command(
            self.executable,
            "-c",
            "mcp_servers={}",
            "app-server",
            "--stdio",
        )
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        result = self.request(
            "initialize",
            {
                "clientInfo": {"name": "ophanim-read-only-observer", "version": "1.0"},
                "capabilities": {"experimentalApi": False},
            },
        )
        self.notify("initialized", {})
        return result

    def request(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: float = 120,
    ) -> dict[str, Any]:
        if not self.running:
            raise CodexProtocolError("app-server is not running")
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            event, box = threading.Event(), {}
            self._pending[request_id] = (event, box)
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        if not event.wait(timeout):
            self._pending.pop(request_id, None)
            raise TimeoutError(f"Codex request timed out: {method}")
        if "error" in box:
            raise CodexProtocolError(f"{method}: {box['error']}")
        return box.get("result", {})

    def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def respond(
        self,
        request_id: int | str,
        *,
        result: Optional[dict[str, Any]] = None,
        error: Optional[dict[str, Any]] = None,
    ) -> None:
        """Reply to a Codex-initiated JSON-RPC request.

        The supervisor is the only caller in production.  Keeping this as a
        small protocol primitive makes it impossible for UI code to invent a
        response or bypass the broker's scope checks.
        """
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result or {}
        self._send(message)

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if not process or not process.stdin:
            raise CodexProtocolError("app-server stdin unavailable")
        with self._lock:
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()

    def _read_loop(self) -> None:
        process = self._process
        assert process and process.stdout
        for line in process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle_message(message)
        code = process.poll()
        if not self._stopping:
            self.on_exit(code if code is not None else -1)

    def _handle_message(self, message: dict[str, Any]) -> None:
        """Dispatch one protocol message; unbrokered requests fail closed."""
        if "id" in message and "method" not in message:
            pending = self._pending.pop(message["id"], None)
            if pending:
                event, box = pending
                box.update(message)
                event.set()
        elif "method" in message and "id" in message:
            method = str(message["method"])
            if self.on_request is not None:
                self.on_request(message["id"], method, message.get("params", {}))
            else:
                self.denied_requests.append(method)
                self.respond(
                    message["id"],
                    error={
                        "code": -32001,
                        "message": "Ophanim client has no approval broker",
                    },
                )
                self.on_notification(
                    "observer/requestDenied",
                    {"requestedMethod": method, "reason": "no approval broker"},
                )
        elif "method" in message:
            self.on_notification(str(message["method"]), message.get("params", {}))

    def stop(self) -> None:
        process = self._process
        if not process:
            return
        self._stopping = True
        if process.poll() is None:
            self._terminate_process_tree(process)
        self._process = None

    def terminate_unexpectedly(self) -> None:
        """Inject an App Server failure while preserving normal exit reporting."""
        process = self._process
        if process and process.poll() is None:
            self._terminate_process_tree(process)

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
