"""Phase 2 acceptance scenario using a real isolated fixture repository."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from openjarvis.codex_observer.store import CodexMissionStore
from openjarvis.codex_observer.supervisor import CodexObserverSupervisor
from openjarvis.core.events import EventBus, EventType


class FixtureCodexClient:
    """Deterministic App Server double that exposes the real Phase 2 shapes."""

    def __init__(self) -> None:
        self.running = False
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[int | str, dict[str, Any]]] = []
        self.on_notification = lambda method, params: None
        self.on_request = lambda request_id, method, params: None
        self.on_exit = lambda code: None
        self._turn_number = 0

    def start(self) -> dict[str, Any]:
        self.running = True
        return {"serverInfo": {"name": "fixture-codex", "version": "0.147.0"}}

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-primary"}}
        if method == "turn/start":
            self._turn_number += 1
            return {"turn": {"id": f"turn-{self._turn_number}", "status": "inProgress"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "thread/fork":
            return {"thread": {"id": f"thread-fork-{len(self.requests)}"}}
        if method == "turn/steer":
            return {"turn": {"id": params["expectedTurnId"], "status": "inProgress"}}
        if method == "turn/interrupt":
            return {"turn": {"id": params["turnId"], "status": "interrupted"}}
        if method == "thread/list":
            return {"data": []}
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "turns": []}}
        raise AssertionError(f"unexpected request: {method}")

    def respond(self, request_id: int | str, *, result=None, error=None) -> None:
        self.responses.append((request_id, {"result": result, "error": error}))

    def emit(self, method: str, params: dict[str, Any]) -> None:
        self.on_notification(method, params)

    def request_from_codex(
        self, request_id: int, method: str, params: dict[str, Any]
    ) -> None:
        self.on_request(request_id, method, params)

    def stop(self) -> None:
        self.running = False


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)).replace("\\", "/"): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".ophanim-fork.json"
    }


def _restore(root: Path, snapshot: dict[str, bytes]) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and path.name != ".ophanim-fork.json":
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for relative, content in snapshot.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _write_feature(root: Path, *, correct: bool) -> None:
    implementation = (
        'return "positive" if value > 0 else "non_positive"'
        if correct
        else 'return "negative"'
    )
    (root / "src" / "feature.py").parent.mkdir(parents=True, exist_ok=True)
    (root / "src" / "feature.py").write_text(
        "def classify(value: int) -> str:\n    " + implementation + "\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_feature.py").parent.mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_feature.py").write_text(
        "from src.feature import classify\n\n\n"
        "def test_classify_positive_value():\n"
        "    assert classify(3) == 'positive'\n",
        encoding="utf-8",
    )


def _run_fixture_tests(root: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_feature.py",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def phase2_fixture(tmp_path: Path):
    root = tmp_path / "fixture-repository"
    root.mkdir()
    (root / "README.md").write_text("# isolated fixture\n", encoding="utf-8")
    original = _snapshot(root)
    yield root, original
    _restore(root, original)


def test_phase_2_exit_gate_fixture_repository(phase2_fixture) -> None:
    root, original = phase2_fixture
    database = root.parent / "mission-control.db"
    store = CodexMissionStore(database)
    bus = EventBus(record_history=True)
    client = FixtureCodexClient()
    supervisor = CodexObserverSupervisor(
        store, roots=[str(root.parent)], bus=bus, client=client
    )

    try:
        mission = supervisor.start_mission(
            "Add a small classify feature with tests; stop before dependency "
            "installation or network access.",
            str(root),
            mode="workspace-write",
            budgets={
                "time_seconds": 600,
                "token_limit": 10_000,
                "command_limit": 20,
                "network_limit": 0,
                "workspace_bytes": 100_000,
                "workspace_files": 20,
            },
        )
        start_params = client.requests[0][1]
        turn_params = client.requests[1][1]
        assert start_params["sandbox"] == "workspace-write"
        assert start_params["approvalPolicy"] == "on-request"
        assert turn_params["sandboxPolicy"] == {
            "type": "workspaceWrite",
            "networkAccess": False,
            "writableRoots": [str(root.resolve())],
        }
        assert mission["authority"]["network_access"] is False

        # Codex requests dependency installation. Guardian stops it before any
        # network-capable effect can run, and the request carries no bypass.
        client.request_from_codex(
            101,
            "item/commandExecution/requestApproval",
            {
                "threadId": mission["thread_id"],
                "turnId": mission["active_turn_id"],
                "itemId": "install-1",
                "command": "python -m pip install requests",
                "cwd": str(root),
                "reason": "dependency needed",
            },
        )
        install_decision = store.list_decisions(mission["id"])[0]
        assert install_decision["kind"] == "network"
        assert install_decision["status"] == "denied_by_guardian"
        assert install_decision["guardian_allows"] is False
        assert client.responses[-1][1]["result"] == {"decision": "decline"}
        assert "pip install" not in " ".join(str(item) for item in mission["commands"])

        # Marc steers Codex directly toward the failing test and can request a
        # structured checkpoint without changing the authority record.
        supervisor.steer_mission(
            mission["id"], "Prioritize the failing test before any refactor."
        )
        supervisor.request_checkpoint(mission["id"])
        assert client.requests[-1][0] == "turn/steer"
        assert "changed_files" in client.requests[-1][1]["input"][0]["text"]

        # A fork gets a physically separate copy and a distinct writable root.
        fork = supervisor.fork_mission(mission["id"])
        assert Path(fork["workspace"]).resolve() != root.resolve()
        assert fork["authority"]["writable_roots"] == [fork["workspace"]]
        assert (
            Path(fork["workspace"], "README.md").read_text(encoding="utf-8")
            == "# isolated fixture\n"
        )
        assert client.requests[-1][1]["sandbox"] == "workspace-write"

        # Decline one in-scope file operation. Reusing it or asking for a
        # broader permission cannot turn the original decline into approval.
        client.request_from_codex(
            102,
            "item/fileChange/requestApproval",
            {
                "threadId": mission["thread_id"],
                "turnId": mission["active_turn_id"],
                "itemId": "change-declined",
                "grantRoot": str(root),
                "reason": "first implementation approach",
            },
        )
        scoped = store.list_decisions(mission["id"])[-1]
        assert scoped["status"] == "pending"
        supervisor.resolve_decision(scoped["id"], {"decision": "decline"})
        with pytest.raises(ValueError, match="no longer pending"):
            supervisor.resolve_decision(scoped["id"], {"decision": "acceptForSession"})
        client.request_from_codex(
            103,
            "item/permissions/requestApproval",
            {
                "threadId": mission["thread_id"],
                "turnId": mission["active_turn_id"],
                "itemId": "outside-permission",
                "cwd": str(root),
                "permissions": {
                    "fileSystem": {
                        "entries": [
                            {
                                "access": "write",
                                "path": {
                                    "type": "path",
                                    "path": str(root.parent / "outside"),
                                },
                            }
                        ]
                    },
                    "network": {"enabled": True},
                },
            },
        )
        outside = store.list_decisions(mission["id"])[-1]
        assert outside["status"] == "denied_by_guardian"
        assert outside["guardian_allows"] is False

        # Codex chooses an allowed alternative and asks for a bounded file
        # change. The fixture harness applies the accepted effect locally.
        client.request_from_codex(
            104,
            "item/fileChange/requestApproval",
            {
                "threadId": mission["thread_id"],
                "turnId": mission["active_turn_id"],
                "itemId": "change-allowed",
                "grantRoot": str(root),
                "reason": "simpler implementation",
            },
        )
        allowed = store.list_decisions(mission["id"])[-1]
        assert allowed["guardian_allows"] is True
        supervisor.resolve_decision(allowed["id"], {"decision": "accept"})
        _write_feature(root, correct=False)
        failing = _run_fixture_tests(root)
        assert failing.returncode != 0
        assert "1 failed" in failing.stdout

        client.request_from_codex(
            105,
            "item/fileChange/requestApproval",
            {
                "threadId": mission["thread_id"],
                "turnId": mission["active_turn_id"],
                "itemId": "change-fix",
                "grantRoot": str(root),
                "reason": "fix the failing test",
            },
        )
        supervisor.resolve_decision(
            store.list_decisions(mission["id"])[-1]["id"], {"decision": "accept"}
        )
        _write_feature(root, correct=True)
        passing = _run_fixture_tests(root)
        assert passing.returncode == 0, passing.stdout + passing.stderr

        # Persist one completed effect, interrupt honestly, then resume the
        # same thread in a new turn while explicitly recording the skipped id.
        client.emit(
            "item/completed",
            {
                "threadId": mission["thread_id"],
                "item": {
                    "id": "pytest-1",
                    "type": "commandExecution",
                    "command": "python -m pytest",
                    "status": "completed",
                },
            },
        )
        supervisor.interrupt_mission(mission["id"])
        client.emit(
            "turn/completed",
            {
                "threadId": mission["thread_id"],
                "turn": {"id": mission["active_turn_id"], "status": "interrupted"},
            },
        )
        assert store.get(mission["id"], include_events=False)["status"] == "interrupted"
        resumed = supervisor.resume_mission(
            mission["id"],
            "Return the final verification summary without rerunning completed "
            "effects.",
        )
        resume_control = [
            control for control in resumed["controls"] if control["action"] == "resume"
        ][-1]
        assert "pytest-1" in resume_control["detail"]["skipped_effect_ids"]
        assert client.requests[-1][0] == "turn/start"

        # Give the fork an alternate implementation, compare both workspaces,
        # select it in the ledger, and prove the primary workspace was not
        # contaminated by either fork.
        fork_root = Path(fork["workspace"])
        _write_feature(fork_root, correct=True)
        (fork_root / "src" / "feature.py").write_text(
            "def classify(value: int) -> str:\n"
            "    return {True: 'positive', False: 'non_positive'}[value > 0]\n",
            encoding="utf-8",
        )
        comparison = supervisor.compare_forks(mission["id"], fork["id"])
        assert comparison["workspace_mutated"] is False
        assert any(
            item["path"] == "src/feature.py" for item in comparison["changed_files"]
        )
        selected = supervisor.select_fork(mission["id"], fork["id"])
        assert selected["selected_fork_id"] == fork["id"]
        assert _snapshot(root).keys() == {
            "README.md",
            "src/feature.py",
            "tests/test_feature.py",
        }

        # Notifications are safe metadata only, and the explicit rollback
        # returns the fixture to its exact pre-mission byte snapshot.
        decision_events = [
            event
            for event in bus.history
            if event.event_type == EventType.CODEX_MISSION_DECISION
        ]
        assert decision_events
        assert all("pip install" not in str(event.data) for event in decision_events)
        _restore(root, original)
        assert _snapshot(root) == original
    finally:
        supervisor.stop()
        store.close()


def test_phase_2_broker_routes_only_protocol_offered_request_choices(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "request-fixture"
    workspace.mkdir()
    store = CodexMissionStore(tmp_path / "requests.db")
    client = FixtureCodexClient()
    supervisor = CodexObserverSupervisor(store, roots=[str(tmp_path)], client=client)
    try:
        mission = supervisor.start_mission(
            "exercise every scoped request type",
            str(workspace),
            mode="workspace-write",
        )
        common = {"threadId": mission["thread_id"], "turnId": mission["active_turn_id"]}

        client.request_from_codex(
            201,
            "item/commandExecution/requestApproval",
            {
                **common,
                "itemId": "command-1",
                "command": "python -m pytest -q",
                "cwd": str(workspace),
                "availableDecisions": ["decline"],
            },
        )
        command = store.list_decisions(mission["id"])[-1]
        assert command["kind"] == "command"
        assert command["offered"]["decision_values"] == ["decline"]
        with pytest.raises(ValueError, match="not offered"):
            supervisor.resolve_decision(command["id"], {"decision": "accept"})
        supervisor.resolve_decision(command["id"], {"decision": "decline"})

        client.request_from_codex(
            202,
            "item/fileChange/requestApproval",
            {**common, "itemId": "file-1", "grantRoot": str(workspace)},
        )
        file_change = store.list_decisions(mission["id"])[-1]
        assert file_change["kind"] == "file-change"
        supervisor.resolve_decision(file_change["id"], {"decision": "decline"})

        client.request_from_codex(
            203,
            "item/permissions/requestApproval",
            {
                **common,
                "itemId": "permission-1",
                "cwd": str(workspace),
                "permissions": {
                    "fileSystem": {
                        "entries": [
                            {
                                "access": "write",
                                "path": {"type": "path", "path": str(workspace)},
                            }
                        ]
                    },
                    "network": {"enabled": False},
                },
            },
        )
        permission = store.list_decisions(mission["id"])[-1]
        assert permission["kind"] == "permission"
        supervisor.resolve_decision(permission["id"], {"decision": "grant"})
        assert client.responses[-1][1]["result"]["scope"] == "turn"

        client.request_from_codex(
            204,
            "item/tool/requestUserInput",
            {
                **common,
                "itemId": "input-1",
                "isBlocking": True,
                "questions": [
                    {
                        "id": "approach",
                        "header": "Approach",
                        "question": "Which?",
                        "isSecret": False,
                    },
                    {
                        "id": "token",
                        "header": "Token",
                        "question": "Value?",
                        "isSecret": True,
                    },
                ],
            },
        )
        user_input = store.list_decisions(mission["id"])[-1]
        assert user_input["kind"] == "user-input"
        supervisor.resolve_decision(
            user_input["id"],
            {"answers": {"approach": ["simple"], "token": ["redacted-test-value"]}},
        )
        assert client.responses[-1][1]["result"]["answers"].keys() == {
            "approach",
            "token",
        }
        assert "redacted-test-value" not in str(supervisor.notifications)
        assert "redacted-test-value" not in str(store.get(mission["id"]))

        client.request_from_codex(
            205,
            "mcpServer/elicitation/request",
            {
                **common,
                "availableDecisions": ["decline"],
                "message": "Optional fixture clarification",
            },
        )
        elicitation = store.list_decisions(mission["id"])[-1]
        assert elicitation["offered"]["decision_values"] == ["decline"]
        supervisor.resolve_decision(elicitation["id"], {"decision": "decline"})
        assert client.responses[-1][1]["result"] == {"action": "decline"}
    finally:
        supervisor.stop()
        store.close()


def test_phase_2_request_delivery_is_idempotent_and_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "boundary-fixture"
    workspace.mkdir()
    store = CodexMissionStore(tmp_path / "boundary.db")
    client = FixtureCodexClient()
    supervisor = CodexObserverSupervisor(store, roots=[str(tmp_path)], client=client)
    try:
        mission = supervisor.start_mission(
            "exercise request delivery boundaries",
            str(workspace),
            mode="workspace-write",
        )
        common = {"threadId": mission["thread_id"], "turnId": mission["active_turn_id"]}

        client.request_from_codex(
            301,
            "item/commandExecution/requestApproval",
            {
                **common,
                "turnId": "stale-turn",
                "itemId": "stale-command",
                "command": "pytest -q",
                "cwd": str(workspace),
            },
        )
        stale = store.list_decisions(mission["id"])[-1]
        assert stale["status"] == "denied_by_guardian"
        assert (
            stale["guardian_reason"]
            == "Codex request targets a stale or non-active turn"
        )
        assert client.responses[-1][1]["result"] == {"decision": "decline"}

        client.request_from_codex(
            301,
            "item/commandExecution/requestApproval",
            {
                **common,
                "turnId": "stale-turn",
                "itemId": "stale-command",
                "command": "pytest -q",
                "cwd": str(workspace),
            },
        )
        assert len(store.list_decisions(mission["id"])) == 1
        assert client.responses[-1][1]["result"] == {"decision": "decline"}

        client.request_from_codex(
            302,
            "item/permissions/requestApproval",
            {
                **common,
                "itemId": "malformed-permission",
                "permissions": {"fileSystem": {"entries": {}}},
            },
        )
        malformed_permission = store.list_decisions(mission["id"])[-1]
        assert malformed_permission["status"] == "denied_by_guardian"
        assert "malformed" in malformed_permission["guardian_reason"]

        client.request_from_codex(
            303,
            "item/tool/requestUserInput",
            {**common, "itemId": "malformed-input", "questions": []},
        )
        malformed_input = store.list_decisions(mission["id"])[-1]
        assert malformed_input["status"] == "denied_by_guardian"
        assert "valid questions" in malformed_input["guardian_reason"]

        client.request_from_codex(
            304,
            "item/fileChange/requestApproval",
            {
                **common,
                "itemId": "pending-file-change",
                "grantRoot": str(workspace),
            },
        )
        pending = store.list_decisions(mission["id"])[-1]
        assert pending["status"] == "pending"
        client.request_from_codex(
            304,
            "item/fileChange/requestApproval",
            {
                **common,
                "itemId": "pending-file-change",
                "grantRoot": str(workspace),
            },
        )
        assert len(store.list_decisions(mission["id"])) == 4
    finally:
        supervisor.stop()
        store.close()


def test_phase_2_resume_refuses_budget_exhaustion_discovered_after_interrupt(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "budget-fixture"
    workspace.mkdir()
    store = CodexMissionStore(tmp_path / "budget.db")
    client = FixtureCodexClient()
    supervisor = CodexObserverSupervisor(store, roots=[str(tmp_path)], client=client)
    try:
        mission = supervisor.start_mission(
            "resume only within the original budget",
            str(workspace),
            mode="workspace-write",
            budgets={"workspace_bytes": 1},
        )
        store.update(mission["id"], status="interrupted", phase="interrupted")
        (workspace / "outside-budget.txt").write_text("too large", encoding="utf-8")
        request_count = len(client.requests)
        with pytest.raises(ValueError, match="budget has been exceeded"):
            supervisor.resume_mission(mission["id"], "continue")
        assert len(client.requests) == request_count
        assert store.get(mission["id"], include_events=False)["status"] == "interrupted"
    finally:
        supervisor.stop()
        store.close()


def test_phase_2_rollback_removes_decision_tables_only(tmp_path: Path) -> None:
    workspace = tmp_path / "rollback-fixture"
    workspace.mkdir()
    database = tmp_path / "rollback.db"
    store = CodexMissionStore(database)
    client = FixtureCodexClient()
    supervisor = CodexObserverSupervisor(store, roots=[str(tmp_path)], client=client)
    try:
        mission = supervisor.start_mission(
            "create rollback evidence",
            str(workspace),
            mode="workspace-write",
        )
        client.request_from_codex(
            401,
            "item/fileChange/requestApproval",
            {
                "threadId": mission["thread_id"],
                "turnId": mission["active_turn_id"],
                "itemId": "rollback-change",
                "grantRoot": str(workspace),
            },
        )
        assert store.list_decisions(mission["id"])
    finally:
        supervisor.stop()
        store.close()

    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE user_owned (value TEXT)")
    connection.execute("INSERT INTO user_owned VALUES ('preserved')")
    connection.commit()
    connection.close()

    CodexMissionStore.rollback_database(database)

    connection = sqlite3.connect(database)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    preserved = connection.execute("SELECT value FROM user_owned").fetchone()[0]
    connection.close()
    assert preserved == "preserved"
    assert not any(name.startswith("codex_") for name in tables)
