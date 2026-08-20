"""Codex mission supervisor and Phase 2 human-control broker.

Read-only missions retain the Phase 1 boundary. Workspace-write missions are
explicit, root-scoped, network-disabled, budgeted, and still require the
Codex protocol's own request/response approval flow.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

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
    "thread/fork",
    "turn/start",
    "turn/steer",
    "turn/interrupt",
}
SERVER_REQUEST_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "item/tool/requestUserInput",
    "mcpServer/elicitation/request",
}
DEFAULT_BUDGETS: dict[str, int] = {
    "time_seconds": 1_800,
    "token_limit": 100_000,
    "command_limit": 200,
    "network_limit": 0,
    "workspace_bytes": 50_000_000,
    "workspace_files": 2_000,
}
ACTIVE_STATUSES = {
    "running",
    "observing",
    "detached",
    "waiting_decision",
    "interrupting",
}
TERMINAL_STATUSES = {
    "completed",
    "interrupted",
    "blocked",
    "failed",
    "cancelled",
    "budget_exceeded",
}
NETWORK_MARKERS = (
    "pip install",
    "python -m pip",
    "npm install",
    "npm i ",
    "pnpm add",
    "yarn add",
    "curl ",
    "wget ",
    "invoke-webrequest",
    " irm ",
    "git clone",
    "git fetch",
    "git pull",
)


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
        "phase2": True,
        "server_request_methods": sorted(SERVER_REQUEST_METHODS),
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
        notification_channels: Optional[list[str]] = None,
        notification_sink: Optional[Callable[[dict[str, Any]], None]] = None,
        guardian_bridge: Optional[Any] = None,
    ) -> None:
        self.store = store
        self.roots = [Path(root).resolve() for root in roots]
        self.bus = bus
        self.notification_channels = notification_channels or ["desktop"]
        self.notification_sink = notification_sink
        self.guardian_bridge = guardian_bridge
        self.notifications: list[dict[str, Any]] = []
        self._budget_interrupts: set[str] = set()
        self.capabilities = (
            detect_codex(executable)
            if client is None
            else {
                "version": "test",
                "protocol": "v2",
                "mode": "read-only-observer",
                "methods": sorted(REQUIRED_METHODS),
                "schema_sha256": "test",
                "phase2": True,
                "server_request_methods": sorted(SERVER_REQUEST_METHODS),
            }
        )
        self.client = client or CodexAppServerClient(executable)
        self.client.on_notification = self._on_notification
        self.client.on_request = self._on_request
        self.client.on_exit = self._on_exit
        self._active_mission: Optional[str] = None

    def _workspace(self, workspace: str) -> str:
        resolved = Path(workspace).resolve()
        if not any(resolved == root or root in resolved.parents for root in self.roots):
            raise ObserverScopeError("workspace is outside configured observer roots")
        return str(resolved)

    def _mission_workspace(self, mission: dict[str, Any]) -> str:
        workspace = Path(mission["workspace"]).resolve()
        try:
            return self._workspace(str(workspace))
        except ObserverScopeError:
            if mission.get("parent_mission_id") and workspace.is_dir():
                try:
                    marker = json.loads(
                        (workspace / ".ophanim-fork.json").read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    marker = {}
                if marker.get("mission_id") == mission["id"]:
                    return str(workspace)
            raise

    @staticmethod
    def _budgets(budgets: Optional[dict[str, Any]]) -> dict[str, int]:
        result = dict(DEFAULT_BUDGETS)
        for key, value in (budgets or {}).items():
            if key not in result:
                raise ValueError(f"unsupported mission budget: {key}")
            if not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"budget {key} must be a non-negative number")
            result[key] = int(value)
        return result

    @staticmethod
    def _authority(mode: str, cwd: str) -> dict[str, Any]:
        if mode == "read-only":
            return {
                "sandbox": "read-only",
                "approval_policy": "never",
                "network_access": False,
                "writable_roots": [],
            }
        return {
            "sandbox": "workspace-write",
            "approval_policy": "on-request",
            "network_access": False,
            "writable_roots": [cwd],
            "approvals_reviewer": "user",
        }

    def _thread_start_params(self, cwd: str, mode: str) -> dict[str, Any]:
        if mode == "read-only":
            return {
                "cwd": cwd,
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "threadSource": "appServer",
            }
        return {
            "cwd": cwd,
            "sandbox": "workspace-write",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "threadSource": "appServer",
        }

    def _turn_start_params(
        self, thread_id: str, message: str, cwd: str, mode: str
    ) -> dict[str, Any]:
        if mode == "read-only":
            return {
                "threadId": thread_id,
                "input": [{"type": "text", "text": message}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            }
        return {
            "threadId": thread_id,
            "input": [{"type": "text", "text": message}],
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "sandboxPolicy": {
                "type": "workspaceWrite",
                "networkAccess": False,
                "writableRoots": [cwd],
            },
        }

    @staticmethod
    def _thread_resume_params(thread_id: str, cwd: str, mode: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": cwd,
            "sandbox": "read-only" if mode == "read-only" else "workspace-write",
            "approvalPolicy": "never" if mode == "read-only" else "on-request",
        }
        if mode == "workspace-write":
            params["approvalsReviewer"] = "user"
        return params

    def ensure_started(self) -> None:
        if not self.client.running:
            self.client.start()

    def start_mission(
        self,
        objective: str,
        workspace: str,
        *,
        mode: str = "read-only",
        budgets: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        cwd = self._workspace(workspace)
        if mode not in {"read-only", "workspace-write"}:
            raise ValueError("mission mode must be read-only or workspace-write")
        budget_payload = self._budgets(budgets)
        mission_id = self.store.create_mission(
            objective,
            cwd,
            mode=mode,
            budgets=budget_payload,
            authority=self._authority(mode, cwd),
        )
        self.store.update(
            mission_id,
            budget_state_json={
                "baseline": {
                    "workspace_bytes": self._workspace_bytes(Path(cwd)),
                    "workspace_files": self._workspace_file_count(Path(cwd)),
                }
            },
        )
        self._active_mission = mission_id
        try:
            self.ensure_started()
            thread_result = self.client.request(
                "thread/start", self._thread_start_params(cwd, mode)
            )
            thread = thread_result.get("thread", thread_result)
            self.store.bind(mission_id, thread_id=thread["id"])
            if isinstance(self.client, CodexAppServerClient):
                # The desktop App Server can defer the turn acknowledgement
                # while its optional desktop integrations initialize.  The
                # thread is already real and scoped at this point, so expose
                # that truthful state instead of holding the Operations UI.
                self.store.update(
                    mission_id,
                    status="running",
                    phase="starting",
                    progress="Codex opened the scoped thread and is preparing the turn",
                )
                threading.Thread(
                    target=self._start_live_turn,
                    args=(mission_id, thread["id"], objective, cwd, mode),
                    daemon=True,
                ).start()
                return self.store.get(mission_id)
            turn_result = self.client.request(
                "turn/start",
                self._turn_start_params(thread["id"], objective, cwd, mode),
            )
            self.store.bind(
                mission_id, turn_id=turn_result.get("turn", turn_result).get("id")
            )
            self.store.update(
                mission_id,
                status="running",
                phase="working" if mode == "workspace-write" else "investigating",
                progress=(
                    "Codex is working in the bounded workspace; "
                    "network access is disabled"
                    if mode == "workspace-write"
                    else "Codex is inspecting the workspace in read-only mode"
                ),
            )
        except TimeoutError:
            # The desktop App Server may finish initializing its bundled MCP
            # servers after it has accepted turn/start.  The request is then
            # slow to acknowledge even though the turn has already been
            # submitted.  Preserve the mission as active rather than
            # misreporting that accepted work as interrupted.
            self.store.update(
                mission_id,
                status="running",
                phase="starting",
                progress="Codex accepted the mission and is preparing its tools",
            )
        except Exception as exc:
            self.store.update(
                mission_id,
                status="interrupted",
                phase="interrupted",
                progress="Codex mission could not continue",
                interrupted_reason=type(exc).__name__,
            )
            raise
        return self.store.get(mission_id)

    def _start_live_turn(
        self, mission_id: str, thread_id: str, objective: str, cwd: str, mode: str
    ) -> None:
        """Finish a real App Server turn without blocking the web request."""
        try:
            turn_result = self.client.request(
                "turn/start", self._turn_start_params(thread_id, objective, cwd, mode)
            )
            turn = turn_result.get("turn", turn_result)
            self.store.bind(mission_id, turn_id=turn.get("id"))
            self.store.update(
                mission_id,
                status="running",
                phase="working" if mode == "workspace-write" else "investigating",
                progress=(
                    "Codex is working in the bounded workspace; "
                    "network access is disabled"
                    if mode == "workspace-write"
                    else "Codex is inspecting the workspace in read-only mode"
                ),
            )
        except Exception as exc:
            self.store.update(
                mission_id,
                status="interrupted",
                phase="interrupted",
                progress="Codex mission could not continue",
                interrupted_reason=type(exc).__name__,
            )

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
        self._mission_workspace(mission)
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
            active_writer = True
            result = self.client.request(
                "thread/read", {"threadId": mission["thread_id"], "includeTurns": True}
            )
        thread = result.get("thread", result)
        turns = thread.get("turns", []) if isinstance(thread, dict) else []
        active_turn = next(
            (turn for turn in turns if turn.get("id") == mission.get("active_turn_id")),
            None,
        )
        recovered_status = (active_turn or {}).get("status", "observing")
        if recovered_status in {"failed", "interrupted", "cancelled"}:
            status, phase, progress = (
                "interrupted",
                "interrupted",
                f"Recovered thread; prior turn is {recovered_status}",
            )
        elif recovered_status == "completed":
            status, phase, progress = (
                "completed",
                "complete",
                "Recovered thread; prior turn had completed",
            )
        elif active_writer:
            status, phase, progress = (
                "detached",
                "active_elsewhere",
                "Recovered ledger; Codex reports an active writer elsewhere",
            )
        else:
            status, phase, progress = (
                "observing",
                "recovered",
                "Reconnected to the persisted Codex thread",
            )
        self.store.update(mission_id, status=status, phase=phase, progress=progress)
        return redact(result)

    def steer_mission(self, mission_id: str, text: str) -> dict[str, Any]:
        message = text.strip()
        if not message:
            raise ValueError("steering text is required")
        if len(message) > 4_000:
            raise ValueError("steering text exceeds the 4,000-character limit")
        mission = self.store.get(mission_id, include_events=False)
        if mission["status"] not in ACTIVE_STATUSES:
            raise ValueError("only an active mission can be steered")
        if not mission["thread_id"] or not mission["active_turn_id"]:
            raise ValueError("mission has no active Codex turn")
        self._mission_workspace(mission)
        self.check_budgets(mission_id)
        if (
            self.store.get(mission_id, include_events=False)["status"]
            == "budget_exceeded"
        ):
            raise ValueError("mission budget has been exceeded")
        self._active_mission = mission_id
        self.ensure_started()
        result = self.client.request(
            "turn/steer",
            {
                "threadId": mission["thread_id"],
                "expectedTurnId": mission["active_turn_id"],
                "input": [{"type": "text", "text": message}],
            },
        )
        self.store.record_control(mission_id, "steer", {"text": redact(message)})
        self.store.update(
            mission_id,
            status="running",
            phase="directed",
            progress="Marc sent new direction to the active Codex turn",
        )
        return redact(result)

    def interrupt_mission(self, mission_id: str) -> dict[str, Any]:
        mission = self.store.get(mission_id, include_events=False)
        if mission["status"] not in ACTIVE_STATUSES:
            raise ValueError("only an active mission can be interrupted")
        if not mission["thread_id"] or not mission["active_turn_id"]:
            raise ValueError("mission has no active Codex turn")
        self._mission_workspace(mission)
        self._active_mission = mission_id
        self.ensure_started()
        result = self.client.request(
            "turn/interrupt",
            {"threadId": mission["thread_id"], "turnId": mission["active_turn_id"]},
        )
        self.store.record_control(mission_id, "interrupt", {})
        self.store.update(
            mission_id,
            status="interrupting",
            phase="interrupting",
            progress="Marc requested that Codex stop the active turn",
        )
        return redact(result)

    def resume_mission(self, mission_id: str, instruction: str) -> dict[str, Any]:
        message = instruction.strip()
        if not message:
            raise ValueError("resume instruction is required")
        mission = self.store.get(mission_id, include_events=False)
        if mission["status"] not in TERMINAL_STATUSES:
            raise ValueError("only a terminal mission can be resumed")
        if mission["status"] == "budget_exceeded":
            raise ValueError(
                "increase the persisted budget through a new mission; "
                "resume cannot bypass it"
            )
        if not mission["thread_id"]:
            raise ValueError("mission has no persisted Codex thread to resume")
        cwd = self._mission_workspace(mission)
        if self.check_budgets(mission_id)["exceeded"]:
            raise ValueError("mission budget has been exceeded")
        self._active_mission = mission_id
        self.ensure_started()
        mode = mission.get("mode", "read-only")
        self.client.request(
            "thread/resume", self._thread_resume_params(mission["thread_id"], cwd, mode)
        )
        skipped = [
            item.get("item_id")
            for item in mission.get("effects", [])
            if item.get("item_id")
        ]
        guarded_message = message
        if skipped:
            guarded_message = (
                f"{message}\n\nOphanim resume guard: completed effect IDs are "
                "already applied and must not be repeated: "
                f"{json.dumps(skipped)}"
            )
        turn_result = self.client.request(
            "turn/start",
            self._turn_start_params(mission["thread_id"], guarded_message, cwd, mode),
        )
        turn = turn_result.get("turn", turn_result)
        self.store.record_control(
            mission_id,
            "resume",
            {"instruction": redact(message), "skipped_effect_ids": skipped},
        )
        self.store.bind(mission_id, turn_id=turn.get("id"))
        self.store.update(
            mission_id,
            status="running",
            phase="resumed",
            progress=(
                "Marc resumed the persisted thread in a new turn; "
                "completed effects were not replayed"
            ),
            interrupted_reason=None,
        )
        return self.store.get(mission_id)

    def request_checkpoint(self, mission_id: str) -> dict[str, Any]:
        mission = self.store.get(mission_id, include_events=False)
        if mission["status"] not in ACTIVE_STATUSES:
            raise ValueError("checkpoint requires an active mission")
        if not mission["thread_id"] or not mission["active_turn_id"]:
            raise ValueError("mission has no active Codex turn")
        self._mission_workspace(mission)
        if self.check_budgets(mission_id)["exceeded"]:
            raise ValueError("mission budget has been exceeded")
        self._active_mission = mission_id
        self.ensure_started()
        prompt = (
            "Pause at the next safe boundary and return a structured checkpoint "
            "with exactly these observable fields: summary, changed_files, "
            "completed_effects, remaining_work, blockers, verification, and "
            "budget_state. Do not include private reasoning."
        )
        result = self.client.request(
            "turn/steer",
            {
                "threadId": mission["thread_id"],
                "expectedTurnId": mission["active_turn_id"],
                "input": [{"type": "text", "text": prompt}],
            },
        )
        self.store.record_control(
            mission_id,
            "checkpoint",
            {
                "fields": [
                    "summary",
                    "changed_files",
                    "completed_effects",
                    "remaining_work",
                    "blockers",
                    "verification",
                    "budget_state",
                ]
            },
        )
        self.store.update(
            mission_id,
            phase="checkpoint_requested",
            progress="Codex was asked for a structured checkpoint",
        )
        return redact(result)

    def fork_mission(self, mission_id: str) -> dict[str, Any]:
        mission = self.store.get(mission_id, include_events=False)
        if not mission["thread_id"]:
            raise ValueError("mission has no Codex thread to fork")
        source = Path(self._mission_workspace(mission))
        fork_parent = Path(tempfile.mkdtemp(prefix="ophanim-codex-fork-"))
        fork_workspace = fork_parent / "workspace"
        try:
            shutil.copytree(
                source,
                fork_workspace,
                ignore=shutil.ignore_patterns(
                    ".ophanim-fork.json", "__pycache__", ".pytest_cache"
                ),
            )
            (fork_workspace / ".ophanim-fork.json").write_text(
                json.dumps({"source": mission["id"]}), encoding="utf-8"
            )
            self.ensure_started()
            mode = mission.get("mode", "read-only")
            result = self.client.request(
                "thread/fork",
                {
                    "threadId": mission["thread_id"],
                    "cwd": str(fork_workspace),
                    "sandbox": "read-only"
                    if mode == "read-only"
                    else "workspace-write",
                    "approvalPolicy": "never" if mode == "read-only" else "on-request",
                    **({} if mode == "read-only" else {"approvalsReviewer": "user"}),
                    "ephemeral": True,
                    "threadSource": "appServer",
                },
            )
        except Exception:
            shutil.rmtree(fork_parent, ignore_errors=True)
            raise
        thread = result.get("thread", result)
        mode = mission.get("mode", "read-only")
        fork_id = self.store.create_mission(
            f"Fork of: {mission['objective']}",
            str(fork_workspace),
            mode=mode,
            budgets=mission.get("budgets", {}),
            authority=self._authority(mode, str(fork_workspace)),
            parent_mission_id=mission["id"],
        )
        self.store.update(
            fork_id,
            budget_state_json={
                "baseline": {
                    "workspace_bytes": self._workspace_bytes(fork_workspace),
                    "workspace_files": self._workspace_file_count(fork_workspace),
                }
            },
        )
        (fork_workspace / ".ophanim-fork.json").write_text(
            json.dumps({"mission_id": fork_id, "source": mission["id"]}),
            encoding="utf-8",
        )
        self.store.record_control(
            mission_id,
            "fork",
            {"fork_mission_id": fork_id, "workspace": str(fork_workspace)},
        )
        self.store.bind(fork_id, thread_id=thread["id"])
        self.store.update(
            fork_id,
            status="paused",
            phase="forked",
            progress=(
                "Isolated fork created with a separate workspace and authority record"
            ),
        )
        return self.store.get(fork_id)

    def compare_forks(self, mission_id: str, other_mission_id: str) -> dict[str, Any]:
        left = self.store.get(mission_id, include_events=False)
        right = self.store.get(other_mission_id, include_events=False)
        parent_ids = {
            left.get("parent_mission_id") or left["id"],
            right.get("parent_mission_id") or right["id"],
        }
        if len(parent_ids) != 1:
            raise ValueError("missions are not sibling forks")
        left_files = self._workspace_hashes(Path(self._mission_workspace(left)))
        right_files = self._workspace_hashes(Path(self._mission_workspace(right)))
        paths = sorted(set(left_files) | set(right_files))
        changed = [
            {"path": path, "left": left_files.get(path), "right": right_files.get(path)}
            for path in paths
            if left_files.get(path) != right_files.get(path)
        ]
        return {
            "parent_mission_id": next(iter(parent_ids)),
            "left_mission_id": left["id"],
            "right_mission_id": right["id"],
            "changed_files": changed,
            "workspace_mutated": False,
        }

    def select_fork(self, mission_id: str, fork_mission_id: str) -> dict[str, Any]:
        parent = self.store.get(mission_id, include_events=False)
        fork = self.store.get(fork_mission_id, include_events=False)
        if fork.get("parent_mission_id") != parent["id"]:
            raise ValueError("fork does not belong to this mission")
        self.store.update(
            parent["id"],
            selected_fork_id=fork["id"],
            progress="Marc selected this fork for follow-up",
        )
        self.store.record_control(
            parent["id"], "select_fork", {"fork_mission_id": fork["id"]}
        )
        return self.store.get(parent["id"])

    @staticmethod
    def _workspace_hashes(workspace: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in workspace.rglob("*"):
            if (
                not path.is_file()
                or ".git" in path.parts
                or path.name == ".ophanim-fork.json"
                or "__pycache__" in path.parts
            ):
                continue
            result[str(path.relative_to(workspace)).replace("\\", "/")] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
            )
        return result

    def _request_kind(self, method: str, params: dict[str, Any]) -> str:
        if method == "item/commandExecution/requestApproval":
            command = str(params.get("command") or "").lower()
            return (
                "network"
                if params.get("networkApprovalContext")
                or any(marker in command for marker in NETWORK_MARKERS)
                else "command"
            )
        if method == "item/fileChange/requestApproval":
            return "file-change"
        if method == "item/permissions/requestApproval":
            return "permission"
        if method in {"item/tool/requestUserInput", "mcpServer/elicitation/request"}:
            return "user-input"
        return "unsupported"

    @staticmethod
    def _offered_values(params: dict[str, Any], defaults: list[str]) -> list[str]:
        supplied = params.get("availableDecisions")
        if supplied is None:
            return defaults
        if not isinstance(supplied, list) or not all(
            isinstance(value, str) for value in supplied
        ):
            return []
        return [value for value in defaults if value in supplied]

    def _offered(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {
                "decision_values": self._offered_values(
                    params, ["accept", "decline", "cancel"]
                )
            }
        if method == "item/permissions/requestApproval":
            return {
                "decision_values": self._offered_values(params, ["grant"]),
                "scope_values": ["turn"],
            }
        if method == "item/tool/requestUserInput":
            questions = params.get("questions")
            return {
                "decision_values": self._offered_values(params, ["answer"]),
                "question_ids": [
                    str(question.get("id"))
                    for question in questions or []
                    if isinstance(question, dict) and question.get("id")
                ],
            }
        if method == "mcpServer/elicitation/request":
            return {
                "decision_values": self._offered_values(
                    params, ["accept", "decline", "cancel"]
                )
            }
        return {"decision_values": []}

    def _request_scope_error(
        self, mission: dict[str, Any], method: str, params: dict[str, Any]
    ) -> Optional[str]:
        if not isinstance(params, dict):
            return "Codex request parameters are malformed"
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str) or not thread_id:
            return "Codex request is missing its thread binding"
        if thread_id != mission.get("thread_id"):
            return "Codex request targets a different persisted thread"
        turn_id = params.get("turnId")
        if not isinstance(turn_id, str) or not turn_id:
            return "Codex request is missing its turn binding"
        if turn_id != mission.get("active_turn_id"):
            return "Codex request targets a stale or non-active turn"
        if method == "item/commandExecution/requestApproval":
            if (
                not isinstance(params.get("command"), str)
                or not params["command"].strip()
            ):
                return "command approval request is malformed"
            if params.get("cwd") is not None and not isinstance(params["cwd"], str):
                return "command approval cwd is malformed"
        elif method == "item/fileChange/requestApproval":
            if params.get("grantRoot") is not None and not isinstance(
                params["grantRoot"], str
            ):
                return "file-change approval root is malformed"
        elif method == "item/permissions/requestApproval":
            permissions = params.get("permissions")
            if not isinstance(permissions, dict):
                return "permission request is malformed"
            network = permissions.get("network")
            if network is not None and not isinstance(network, dict):
                return "permission network scope is malformed"
            filesystem = permissions.get("fileSystem")
            if filesystem is not None and not isinstance(filesystem, dict):
                return "permission filesystem scope is malformed"
            if isinstance(filesystem, dict):
                entries = filesystem.get("entries")
                if entries is not None:
                    if not isinstance(entries, list):
                        return "permission filesystem entries are malformed"
                    for entry in entries:
                        path = entry.get("path") if isinstance(entry, dict) else None
                        if not isinstance(path, dict) or not isinstance(
                            path.get("path"), str
                        ):
                            return "permission filesystem entry is malformed"
                for key in ("read", "write"):
                    values = filesystem.get(key)
                    if values is not None and (
                        not isinstance(values, list)
                        or not all(isinstance(value, str) for value in values)
                    ):
                        return "permission filesystem paths are malformed"
        elif method == "item/tool/requestUserInput":
            questions = params.get("questions")
            if not isinstance(questions, list) or not questions:
                return "user-input request has no valid questions"
            question_ids = []
            for question in questions:
                if not isinstance(question, dict) or not isinstance(
                    question.get("id"), str
                ):
                    return "user-input question is malformed"
                question_ids.append(question["id"])
            if len(question_ids) != len(set(question_ids)):
                return "user-input question IDs must be unique"
        return None

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    def _guardian_check(
        self, mission: dict[str, Any], kind: str, params: dict[str, Any]
    ) -> tuple[bool, str]:
        if mission.get("mode") != "workspace-write":
            return False, "read-only mission authority"
        if mission["status"] in {
            "starting",
            "detached",
            "interrupting",
            "budget_exceeded",
            "interrupted",
            "completed",
            "failed",
            "cancelled",
        }:
            return False, "mission is not active"
        if kind == "network":
            return (
                False,
                "network access is disabled by Guardian and cannot be approved",
            )
        if kind == "command":
            cwd = Path(str(params.get("cwd") or mission["workspace"])).resolve()
            if not self._is_within(cwd, Path(mission["workspace"])):
                return False, "command cwd is outside the mission workspace"
            if len(mission.get("commands", [])) >= mission.get("budgets", {}).get(
                "command_limit", DEFAULT_BUDGETS["command_limit"]
            ):
                return False, "command budget is exhausted"
        elif kind == "file-change":
            root = params.get("grantRoot")
            if root and not self._is_within(
                Path(root).resolve(), Path(mission["workspace"])
            ):
                return False, "file-change root is outside the mission workspace"
        elif kind == "permission":
            permissions = params.get("permissions", {})
            if not isinstance(permissions, dict):
                return False, "permission request is malformed"
            network = permissions.get("network", {})
            if isinstance(network, dict) and network.get("enabled"):
                return False, "Guardian never grants network access in Phase 2"
            fs = permissions.get("fileSystem", {})
            if not isinstance(fs, dict):
                return False, "permission filesystem scope is malformed"
            for entry in fs.get("entries", []):
                path_value = (
                    (entry.get("path") or {}).get("path")
                    if isinstance(entry.get("path"), dict)
                    else None
                )
                if path_value and not self._is_within(
                    Path(path_value).resolve(), Path(mission["workspace"])
                ):
                    return (
                        False,
                        "requested filesystem permission is outside the mission "
                        "workspace",
                    )
            for legacy_key in ("read", "write"):
                for path_value in fs.get(legacy_key, []):
                    if not self._is_within(
                        Path(path_value).resolve(), Path(mission["workspace"])
                    ):
                        return (
                            False,
                            "requested filesystem permission is outside the mission "
                            "workspace",
                        )
        return True, "within the mission workspace, authority, and current budgets"

    def _safe_request_display(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            params = {}
        display = redact(params)
        if method == "item/tool/requestUserInput":
            questions = params.get("questions", [])
            if not isinstance(questions, list):
                questions = []
            display = {
                "threadId": params.get("threadId"),
                "turnId": params.get("turnId"),
                "questions": [
                    {
                        "id": q.get("id"),
                        "header": q.get("header"),
                        "question": q.get("question"),
                        "options": q.get("options", []),
                        "is_secret": bool(q.get("isSecret")),
                    }
                    for q in questions
                    if isinstance(q, dict)
                ],
            }
        return display

    def _on_request(
        self, request_id: int | str, method: str, params: dict[str, Any]
    ) -> None:
        if not isinstance(params, dict):
            params = {}
        thread_id = params.get("threadId")
        mission_id = (
            self.store.mission_for_thread(thread_id)
            if isinstance(thread_id, str) and thread_id
            else None
        )
        if not mission_id:
            self._respond_error(
                request_id, "request is not bound to a persisted mission"
            )
            return
        mission = self.store.get(mission_id, include_events=False)
        kind = self._request_kind(method, params)
        offered = self._offered(method, params)
        scope_reason = self._request_scope_error(mission, method, params)
        if scope_reason:
            guardian_allowed, guardian_reason = False, scope_reason
        else:
            guardian_allowed, guardian_reason = self._guardian_check(
                mission, kind, params
            )
        if not offered.get("decision_values"):
            guardian_allowed = False
            guardian_reason = "Codex offered no supported decision for this request"
        requested = self._safe_request_display(method, params)
        existing = self.store.get_decision_for_request(
            mission_id, request_id, include_response=True
        )
        if existing:
            if (
                existing["request_method"] != method
                or existing["thread_id"] != thread_id
                or existing["turn_id"] != params.get("turnId")
                or existing["requested"] != requested
            ):
                self._respond_error(
                    request_id, "request ID was reused for a different request"
                )
                return
            if existing["status"] == "pending":
                return
            if existing.get("response") is not None:
                if hasattr(self.client, "respond"):
                    self.client.respond(request_id, result=existing["response"])
            else:
                self._respond_error(request_id, "Codex request was already resolved")
            return
        decision_id, created = self.store.record_decision_if_absent(
            mission_id,
            request_id=request_id,
            request_method=method,
            kind=kind,
            thread_id=str(thread_id) if thread_id else mission.get("thread_id"),
            turn_id=str(params.get("turnId"))
            if params.get("turnId")
            else mission.get("active_turn_id"),
            item_id=str(params.get("itemId")) if params.get("itemId") else None,
            requested=requested,
            offered=offered,
            guardian_allowed=guardian_allowed,
            guardian_reason=guardian_reason,
        )
        if not created:
            existing = self.store.get_decision(decision_id, include_response=True)
            if existing["status"] == "pending":
                return
            if existing.get("response") is not None:
                if hasattr(self.client, "respond"):
                    self.client.respond(request_id, result=existing["response"])
            else:
                self._respond_error(request_id, "Codex request was already resolved")
            return
        status = (
            "pending"
            if guardian_allowed and kind != "unsupported"
            else "denied_by_guardian"
        )
        public = {
            "decision_id": decision_id,
            "mission_id": mission_id,
            "kind": kind,
            "status": status,
            "codex_requested": True,
            "guardian_allows": guardian_allowed,
            "offered_count": len(offered.get("decision_values", [])),
            "summary": f"Codex requested a {kind} decision",
        }
        self.notifications.append(public)
        for channel in self.notification_channels:
            self.store.record_notification(
                mission_id, decision_id, channel, "sent", public
            )
        if self.notification_sink:
            self.notification_sink(dict(public))
        if self.bus:
            self.bus.publish(EventType.CODEX_MISSION_DECISION, public)
        if guardian_allowed and kind != "unsupported":
            self.store.update(
                mission_id,
                status="waiting_decision",
                phase="waiting_decision",
                progress=public["summary"],
            )
            return
        denial_response = self._denial_response(method)
        self.store.resolve_decision(
            decision_id,
            status=status if kind != "unsupported" else "unsupported",
            marc_decision={
                "actor": "guardian",
                "decision": "declined",
                "reason": guardian_reason,
            },
            response=denial_response,
        )
        self._respond_denial(request_id, method, guardian_reason)
        guardian_action_id = self._record_guardian_denial(
            mission,
            decision_id,
            request_id,
            kind,
            self._safe_request_display(method, params),
        )
        self.store.record_control(
            mission_id,
            "decision_denied",
            {
                "decision_id": decision_id,
                "reason": guardian_reason,
                "guardian_action_id": guardian_action_id,
            },
        )

    def _record_guardian_denial(
        self,
        mission: dict[str, Any],
        decision_id: str,
        request_id: int | str,
        kind: str,
        requested: dict[str, Any],
    ) -> Optional[str]:
        if self.guardian_bridge is None or kind == "user-input":
            return None
        decision = self.guardian_bridge.deny(
            mission_id=mission["id"],
            decision_id=decision_id,
            request_id=request_id,
            kind=kind,
            workspace=mission["workspace"],
            requested=requested,
        )
        return decision.authorization.action_id

    def _respond_error(self, request_id: int | str, reason: str) -> None:
        if hasattr(self.client, "respond"):
            self.client.respond(request_id, error={"code": -32001, "message": reason})

    @staticmethod
    def _denial_response(method: str) -> Optional[dict[str, Any]]:
        if method == "mcpServer/elicitation/request":
            return {"action": "decline"}
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "decline"}
        if method == "item/permissions/requestApproval":
            return {
                "permissions": {"network": {"enabled": False}},
                "scope": "turn",
            }
        return None

    def _respond_denial(self, request_id: int | str, method: str, reason: str) -> None:
        if not hasattr(self.client, "respond"):
            return
        response = self._denial_response(method)
        if response is not None:
            self.client.respond(request_id, result=response)
        else:
            self._respond_error(request_id, reason)

    def resolve_decision(
        self, decision_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        decision = self.store.get_decision(decision_id)
        if decision["status"] != "pending":
            raise ValueError("decision is no longer pending")
        value = payload.get("decision")
        offered = decision["offered"]
        method = decision["request_method"]
        if method == "item/tool/requestUserInput":
            answers = payload.get("answers")
            if not isinstance(answers, dict) or set(answers) != set(
                offered.get("question_ids", [])
            ):
                raise ValueError(
                    "answers must match the questions actually offered by Codex"
                )
            response = {
                "answers": {
                    key: {
                        "answers": [str(item) for item in value]
                        if isinstance(value, list)
                        else [str(value)]
                    }
                    for key, value in answers.items()
                }
            }
            status = "approved"
            marc_decision = {
                "actor": "Marc",
                "decision": "answer",
                "question_ids": sorted(answers),
            }
        elif method == "item/permissions/requestApproval":
            if value not in offered.get("decision_values", []):
                raise ValueError("decision is not offered by Codex")
            if not decision["guardian_allowed"]:
                raise ValueError("Guardian does not allow this permission request")
            requested_permissions = decision["requested"].get("permissions", {})
            response = {
                "permissions": {
                    "fileSystem": requested_permissions.get("fileSystem", {}),
                    "network": {"enabled": False},
                },
                "scope": "turn",
                "strictAutoReview": True,
            }
            status, marc_decision = (
                "approved",
                {"actor": "Marc", "decision": value, "scope": "turn"},
            )
        else:
            if value not in offered.get("decision_values", []):
                raise ValueError("decision is not offered by Codex")
            if (
                value
                in {
                    "accept",
                    "acceptForSession",
                    "acceptWithExecpolicyAmendment",
                    "applyNetworkPolicyAmendment",
                }
                and not decision["guardian_allowed"]
            ):
                raise ValueError("Guardian does not allow this request")
            response = (
                {"action": value}
                if method == "mcpServer/elicitation/request"
                else {"decision": value}
            )
            status = (
                "approved"
                if value in {"accept", "acceptForSession", "grant", "answer"}
                else "declined"
            )
            marc_decision = {"actor": "Marc", "decision": value}
        guardian_action_id = None
        if status == "approved" and decision["kind"] != "user-input":
            if self.guardian_bridge is not None:
                guardian_decision = self.guardian_bridge.authorize(
                    mission_id=decision["mission_id"],
                    decision_id=decision_id,
                    request_id=decision["request_id"],
                    kind=decision["kind"],
                    workspace=self.store.get(
                        decision["mission_id"], include_events=False
                    )["workspace"],
                    requested=decision["requested"],
                )
                if not guardian_decision.allowed:
                    raise ValueError(guardian_decision.reason)
                guardian_action_id = guardian_decision.authorization.action_id
        if hasattr(self.client, "respond"):
            self.client.respond(decision["request_id"], result=response)
        self.store.resolve_decision(
            decision_id, status=status, marc_decision=marc_decision, response=response
        )
        self.store.record_control(
            decision["mission_id"],
            "decision_resolved",
            {
                "decision_id": decision_id,
                "status": status,
                "marc_decision": marc_decision,
                "guardian_action_id": guardian_action_id,
            },
        )
        if (
            self.store.get(decision["mission_id"], include_events=False)["status"]
            == "waiting_decision"
        ):
            self.store.update(
                decision["mission_id"],
                status="running",
                phase="working",
                progress="Codex decision answered within the persisted scope",
            )
        return self.store.get_decision(decision_id)

    def check_budgets(self, mission_id: str) -> dict[str, Any]:
        mission = self.store.get(mission_id, include_events=False)
        usage = mission.get("usage", {})
        prior_budget_state = mission.get("budget_state", {})
        baseline = prior_budget_state.get("baseline", {})
        workspace_bytes = self._workspace_bytes(Path(mission["workspace"]))
        workspace_files = self._workspace_file_count(Path(mission["workspace"]))
        used = {
            "time_seconds": max(
                0, int(time.time() - _parse_timestamp(mission["created_at"]))
            ),
            "token_limit": int(usage.get("total") or usage.get("total_tokens") or 0),
            "command_limit": len(mission.get("commands", [])),
            "network_limit": sum(
                1
                for item in mission.get("tools", [])
                if item.get("type") in {"webSearch", "browser", "network"}
            ),
            "workspace_bytes": max(
                0, workspace_bytes - int(baseline.get("workspace_bytes", 0))
            ),
            "workspace_files": max(
                0, workspace_files - int(baseline.get("workspace_files", 0))
            ),
        }
        limits = mission.get("budgets", {})
        exceeded = [
            key
            for key, value in used.items()
            if key in limits
            and (
                (key == "time_seconds" and value >= limits[key])
                or (key != "time_seconds" and value > limits[key])
            )
        ]
        state = {
            "baseline": baseline,
            "used": used,
            "limits": limits,
            "exceeded": exceeded,
        }
        self.store.update(mission_id, budget_state_json=state)
        if exceeded and mission["status"] not in TERMINAL_STATUSES:
            reason = f"Budget exceeded: {', '.join(exceeded)}"
            self.store.update(
                mission_id,
                status="budget_exceeded",
                phase="budget_exceeded",
                progress=reason,
                interrupted_reason=reason,
            )
            self.store.record_control(
                mission_id, "budget_exceeded", {"categories": exceeded, "state": state}
            )
            if (
                mission.get("active_turn_id")
                and mission_id not in self._budget_interrupts
            ):
                self._budget_interrupts.add(mission_id)
                self._interrupt_for_budget(mission)
        return state

    def _interrupt_for_budget(self, mission: dict[str, Any]) -> None:
        def interrupt() -> None:
            try:
                self.ensure_started()
                self.client.request(
                    "turn/interrupt",
                    {
                        "threadId": mission["thread_id"],
                        "turnId": mission["active_turn_id"],
                    },
                )
            except Exception:
                pass

        if getattr(self.client, "_reader", None) is threading.current_thread():
            threading.Thread(target=interrupt, daemon=True).start()
        else:
            interrupt()

    @staticmethod
    def _workspace_bytes(workspace: Path) -> int:
        total = 0
        if workspace.exists():
            for path in workspace.rglob("*"):
                if (
                    path.is_file()
                    and ".git" not in path.parts
                    and path.name != ".ophanim-fork.json"
                ):
                    try:
                        total += path.stat().st_size
                    except OSError:
                        pass
        return total

    @staticmethod
    def _workspace_file_count(workspace: Path) -> int:
        return (
            sum(
                1
                for path in workspace.rglob("*")
                if path.is_file()
                and ".git" not in path.parts
                and path.name != ".ophanim-fork.json"
            )
            if workspace.exists()
            else 0
        )

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
        if method == "turn/started":
            turn = params.get("turn", params)
            if turn.get("id"):
                self.store.bind(mission_id, turn_id=turn["id"])
        elif method == "turn/plan/updated":
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
            if (
                item.get("id")
                and item_type in {"commandExecution", "fileChange"}
                and not any(
                    effect.get("item_id") == item["id"] for effect in mission["effects"]
                )
            ):
                changes["effects_json"] = [
                    *mission["effects"],
                    {
                        "item_id": item["id"],
                        "type": item_type,
                        "status": item.get("status", "completed"),
                        "event_fingerprint": event_fp,
                    },
                ]
        self.store.update(mission_id, **changes)
        budget_state = self.check_budgets(mission_id)
        if budget_state["exceeded"]:
            summary = f"{summary or 'Codex update'} · budget boundary reached"
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


def _parse_timestamp(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value).timestamp()
