"""Read-only Codex mission supervisor."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from openjarvis.core.events import EventBus, EventType

from .normalizer import normalize
from .protocol import (
    CodexAppServerClient,
    CodexProtocolError,
    executable_command,
    resolve_executable,
)
from .redaction import redact
from .store import CodexMissionStore

REQUIRED_METHODS = {
    "initialize",
    "thread/start",
    "thread/list",
    "thread/read",
    "thread/resume",
    "turn/start",
}


class ObserverScopeError(ValueError):
    pass


def detect_codex(executable: str = "codex") -> dict[str, Any]:
    path = resolve_executable(executable)
    if not path:
        raise FileNotFoundError("Codex CLI is not installed")
    version = subprocess.run(
        executable_command(path, "--version"),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="ophanim-codex-schema-") as directory:
        subprocess.run(
            executable_command(
                path, "app-server", "generate-json-schema", "--out", directory
            ),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        request = Path(directory, "ClientRequest.json").read_text(encoding="utf-8")
        notification = Path(directory, "ServerNotification.json").read_text(
            encoding="utf-8"
        )
        missing = sorted(
            method for method in REQUIRED_METHODS if f'"{method}"' not in request
        )
        if missing:
            raise RuntimeError(f"Codex protocol is missing required methods: {missing}")
        digest = hashlib.sha256((request + notification).encode()).hexdigest()
    return {
        "path": path,
        "version": version,
        "protocol": "v2",
        "schema_sha256": digest,
        "methods": sorted(REQUIRED_METHODS),
        "mode": "read-only-observer",
    }


class CodexObserverSupervisor:
    def __init__(
        self,
        store: CodexMissionStore,
        *,
        roots: list[str],
        bus: Optional[EventBus] = None,
        executable: str = "codex",
        client: Optional[CodexAppServerClient] = None,
    ) -> None:
        self.store = store
        self.roots = [Path(root).resolve() for root in roots]
        self.bus = bus
        self.capabilities = (
            detect_codex(executable)
            if client is None
            else {
                "version": "test",
                "protocol": "v2",
                "mode": "read-only-observer",
                "methods": sorted(REQUIRED_METHODS),
                "schema_sha256": "test",
            }
        )
        self.client = client or CodexAppServerClient(executable)
        self.client.on_notification = self._on_notification
        self.client.on_exit = self._on_exit
        self._active_mission: Optional[str] = None

    def _workspace(self, workspace: str) -> str:
        resolved = Path(workspace).resolve()
        if not any(resolved == root or root in resolved.parents for root in self.roots):
            raise ObserverScopeError("workspace is outside configured observer roots")
        return str(resolved)

    def ensure_started(self) -> None:
        if not self.client.running:
            self.client.start()

    def start_mission(self, objective: str, workspace: str) -> dict[str, Any]:
        cwd = self._workspace(workspace)
        mission_id = self.store.create_mission(objective, cwd)
        self._active_mission = mission_id
        try:
            self.ensure_started()
            thread_result = self.client.request(
                "thread/start",
                {
                    "cwd": cwd,
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                    "threadSource": "appServer",
                },
            )
            thread = thread_result.get("thread", thread_result)
            thread_id = thread["id"]
            self.store.bind(mission_id, thread_id=thread_id)
            turn_result = self.client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": objective}],
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                },
            )
            turn = turn_result.get("turn", turn_result)
            self.store.bind(mission_id, turn_id=turn.get("id"))
            self.store.update(
                mission_id,
                status="running",
                phase="investigating",
                progress="Codex is inspecting the workspace in read-only mode",
            )
        except Exception as exc:
            self.store.update(
                mission_id,
                status="interrupted",
                phase="interrupted",
                progress="Codex observer could not continue",
                interrupted_reason=type(exc).__name__,
            )
            raise
        return self.store.get(mission_id)

    def list_threads(self) -> dict[str, Any]:
        self.ensure_started()
        return self.client.request(
            "thread/list", {"sourceKinds": ["appServer"], "limit": 100}
        )

    def read_thread(self, thread_id: str) -> dict[str, Any]:
        self.ensure_started()
        return self.client.request(
            "thread/read", {"threadId": thread_id, "includeTurns": True}
        )

    def resume_observation(self, mission_id: str) -> dict[str, Any]:
        mission = self.store.get(mission_id, include_events=False)
        self._workspace(mission["workspace"])
        self._active_mission = mission_id
        self.ensure_started()
        active_writer = False
        try:
            result = self.client.request(
                "thread/resume",
                {
                    "threadId": mission["thread_id"],
                    "cwd": mission["workspace"],
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                },
            )
        except CodexProtocolError as exc:
            if "active writer" not in str(exc).lower():
                raise
            # The turn can outlive the stdio bridge. Read it without claiming
            # control and report that detached state truthfully.
            active_writer = True
            result = self.client.request(
                "thread/read",
                {"threadId": mission["thread_id"], "includeTurns": True},
            )
        thread = result.get("thread", result)
        turns = thread.get("turns", []) if isinstance(thread, dict) else []
        active_turn = next(
            (turn for turn in turns if turn.get("id") == mission.get("active_turn_id")),
            None,
        )
        recovered_status = (active_turn or {}).get("status", "observing")
        if recovered_status in {"failed", "interrupted", "cancelled"}:
            status, phase = "interrupted", "interrupted"
            progress = f"Recovered thread; prior turn is {recovered_status}"
        elif recovered_status == "completed":
            status, phase = "completed", "complete"
            progress = "Recovered thread; prior turn had completed"
        elif active_writer:
            status, phase = "detached", "active_elsewhere"
            progress = "Recovered ledger; Codex reports an active writer elsewhere"
        else:
            status, phase = "observing", "recovered"
            progress = "Reconnected to the persisted Codex thread"
        self.store.update(
            mission_id,
            status=status,
            phase=phase,
            progress=progress,
        )
        return redact(result)

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        thread_id = params.get("threadId") or params.get("thread", {}).get("id")
        mission_id = (
            self.store.mission_for_thread(thread_id)
            if thread_id
            else self._active_mission
        )
        if not mission_id:
            return
        contract, display, summary = normalize(method, params)
        event_fp, created = self.store.record_event(
            mission_id, method, params, contract.to_json(), display
        )
        if not created:
            return
        changes: dict[str, Any] = {"progress": summary or f"Observed {method}"}
        if method == "turn/plan/updated":
            changes["plan_json"] = params.get(
                "plan", params.get("turn", {}).get("plan", [])
            )
        elif method in {"thread/tokenUsage/updated", "turn/tokenUsage/updated"}:
            changes["usage_json"] = redact(
                params.get("tokenUsage", params.get("usage", params))
            )
        elif method == "turn/diff/updated":
            mission = self.store.get(mission_id, include_events=False)
            changes["files_json"] = [
                *mission["files"],
                {"type": "aggregatedDiff", "diff": redact(params.get("diff", ""))},
            ]
        elif method in {"error", "turn/error", "observer/requestDenied"}:
            changes.update(
                status="blocked", phase="blocked", errors_json=[display["payload"]]
            )
        elif method == "turn/completed":
            status = params.get("turn", {}).get("status", "completed")
            changes.update(
                status=status,
                phase="complete" if status == "completed" else status,
                verification_state="event_trace_complete",
            )
        elif method == "item/completed":
            item = params.get("item", {})
            item_type = item.get("type")
            mission = self.store.get(mission_id, include_events=False)
            if item_type == "commandExecution":
                changes["commands_json"] = [*mission["commands"], redact(item)]
            elif item_type in {
                "mcpToolCall",
                "dynamicToolCall",
                "webSearch",
                "imageView",
                "browser",
            }:
                changes["tools_json"] = [*mission["tools"], redact(item)]
            elif item_type == "fileChange":
                changes["files_json"] = [*mission["files"], redact(item)]
        self.store.update(mission_id, **changes)
        if summary and self.store.milestone(mission_id, summary, event_fp) and self.bus:
            event_type = (
                EventType.CODEX_MISSION_ERROR
                if method in {"error", "turn/error", "observer/requestDenied"}
                else EventType.CODEX_MISSION_UPDATE
            )
            self.bus.publish(
                event_type,
                {
                    "mission_id": mission_id,
                    "status": changes.get("status", "running"),
                    "summary": summary,
                },
            )

    def _on_exit(self, code: int) -> None:
        if self._active_mission:
            self.store.update(
                self._active_mission,
                status="interrupted",
                phase="interrupted",
                progress="Codex App Server exited; mission state is preserved",
                interrupted_reason=f"app-server exit {code}",
            )

    def stop(self) -> None:
        self.client.stop()
