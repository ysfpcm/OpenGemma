"""Phase 1 unit and integration coverage for the read-only Codex observer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from openjarvis.codex_observer.normalizer import normalize
from openjarvis.codex_observer.protocol import CodexAppServerClient
from openjarvis.codex_observer.redaction import REDACTED, redact
from openjarvis.codex_observer.store import CodexMissionStore
from openjarvis.codex_observer.supervisor import (
    CodexObserverSupervisor,
    ObserverScopeError,
)
from openjarvis.core.events import EventBus, EventType


class FakeClient:
    def __init__(self) -> None:
        self.running = False
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.on_notification = lambda method, params: None
        self.on_exit = lambda code: None

    def start(self) -> dict[str, Any]:
        self.running = True
        return {"serverInfo": {"name": "fake-codex"}}

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        if method == "turn/steer":
            return {"turn": {"id": params["expectedTurnId"], "status": "inProgress"}}
        if method == "turn/interrupt":
            return {"turn": {"id": params["turnId"], "status": "interrupted"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "thread/fork":
            return {"thread": {"id": "thread-2"}}
        if method == "thread/list":
            return {"data": [{"id": "thread-1"}]}
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "turns": []}}
        raise AssertionError(method)

    def emit(self, method: str, params: dict[str, Any]) -> None:
        self.on_notification(method, params)

    def crash(self, code: int = 17) -> None:
        self.running = False
        self.on_exit(code)

    def stop(self) -> None:
        self.running = False


@pytest.fixture
def store(tmp_path: Path):
    ledger = CodexMissionStore(tmp_path / "observer.db", raw_retention_hours=1)
    yield ledger
    ledger.close()


def test_recursive_redaction_covers_keys_and_embedded_secrets() -> None:
    payload = {
        "authorization": "Bearer abcdefghijklmnop",
        "nested": [{"api_key": "sk-abcdefghijklmnop"}],
        "message": "password=hunter2 token:abcdefghijklmno",
        "safe": "visible",
    }
    rendered = json.dumps(redact(payload))
    assert rendered.count(REDACTED) >= 3
    assert "hunter2" not in rendered
    assert "abcdefghijklmnop" not in rendered
    assert "visible" in rendered


def test_normalized_event_is_an_observed_phase_zero_contract() -> None:
    contract, display, summary = normalize(
        "item/completed",
        {"threadId": "t", "item": {"type": "agentMessage", "text": "finding"}},
    )
    assert contract.contract_type == "observation"
    assert contract.source_kind == "observed"
    assert contract.provenance["method"] == "item/completed"
    assert display["item_type"] == "agentMessage"
    assert summary == "Codex produced an observable finding"


def test_reasoning_content_is_not_exposed_as_progress_or_display() -> None:
    contract, display, summary = normalize(
        "item/reasoning/textDelta",
        {
            "threadId": "t",
            "delta": "private chain of thought",
            "item": {"id": "r", "type": "reasoning", "content": "hidden"},
        },
    )
    rendered = json.dumps(display)
    assert "private chain" not in rendered
    assert "hidden" not in rendered
    assert "private chain" not in contract.to_json()
    assert summary is None


def test_observer_forces_read_only_and_tracks_deduplicated_state(
    store: CodexMissionStore, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = FakeClient()
    bus = EventBus(record_history=True)
    observer = CodexObserverSupervisor(
        store, roots=[str(workspace)], bus=bus, client=client
    )
    mission = observer.start_mission("Inspect only; make no changes", str(workspace))
    assert mission["thread_id"] == "thread-1"
    assert mission["active_turn_id"] == "turn-1"
    assert client.requests[0] == (
        "thread/start",
        {
            "cwd": str(workspace.resolve()),
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "threadSource": "appServer",
        },
    )
    turn_params = client.requests[1][1]
    assert turn_params["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
    assert turn_params["approvalPolicy"] == "never"
    assert observer.list_threads()["data"][0]["id"] == "thread-1"
    assert observer.read_thread("thread-1")["thread"]["id"] == "thread-1"

    plan = {
        "threadId": "thread-1",
        "plan": [{"step": "Map context", "status": "inProgress"}],
    }
    command = {
        "threadId": "thread-1",
        "item": {
            "type": "commandExecution",
            "command": "rg managed-agent",
            "status": "completed",
            "authorization": "Bearer abcdefghijklmnop",
        },
    }
    client.emit("turn/plan/updated", plan)
    client.emit("turn/plan/updated", plan)
    client.emit("item/completed", command)
    client.emit(
        "item/completed",
        {
            "threadId": "thread-1",
            "item": {
                "type": "mcpToolCall",
                "name": "read_context",
                "status": "completed",
            },
        },
    )
    client.emit("turn/diff/updated", {"threadId": "thread-1", "diff": "read-only"})
    client.emit(
        "thread/tokenUsage/updated",
        {"threadId": "thread-1", "tokenUsage": {"total": 42}},
    )
    client.emit(
        "turn/completed",
        {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}},
    )

    observed = store.get(mission["id"])
    assert observed["status"] == "completed"
    assert observed["plan"] == plan["plan"]
    assert observed["commands"][0]["authorization"] == REDACTED
    assert observed["usage"] == {"total": 42}
    assert observed["tools"][0]["name"] == "read_context"
    assert observed["files"] == [{"type": "aggregatedDiff", "diff": "read-only"}]
    assert (
        len(
            [
                event
                for event in observed["events"]
                if event["method"] == "turn/plan/updated"
            ]
        )
        == 1
    )
    summaries = [event.data["summary"] for event in bus.history]
    assert summaries.count("Codex updated its observable plan") == 1
    assert all(
        event.event_type == EventType.CODEX_MISSION_UPDATE for event in bus.history
    )


def test_restart_preserves_ledger_and_resumes_same_thread(
    store: CodexMissionStore, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = FakeClient()
    observer = CodexObserverSupervisor(store, roots=[str(workspace)], client=first)
    mission = observer.start_mission("Read only", str(workspace))
    first.emit("turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}})
    first.crash(23)
    interrupted = store.get(mission["id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["interrupted_reason"] == "app-server exit 23"

    replacement = FakeClient()
    recovered = CodexObserverSupervisor(
        store, roots=[str(workspace)], client=replacement
    )
    result = recovered.resume_observation(mission["id"])
    assert result["thread"]["id"] == "thread-1"
    assert replacement.requests[-1][0] == "thread/resume"
    assert replacement.requests[-1][1]["sandbox"] == "read-only"
    assert store.get(mission["id"])["phase"] == "recovered"


def test_phase_two_controls_target_only_the_persisted_active_turn(
    store: CodexMissionStore, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = FakeClient()
    observer = CodexObserverSupervisor(store, roots=[str(workspace)], client=client)
    mission = observer.start_mission("Inspect", str(workspace))

    observer.steer_mission(mission["id"], "Prioritize the failing test")
    assert client.requests[-1] == (
        "turn/steer",
        {"threadId": "thread-1", "expectedTurnId": "turn-1", "input": [{"type": "text", "text": "Prioritize the failing test"}]},
    )
    observer.interrupt_mission(mission["id"])
    assert client.requests[-1] == (
        "turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"}
    )
    controlled = store.get(mission["id"])
    assert controlled["phase"] == "interrupting"
    assert [entry["action"] for entry in controlled["controls"]] == ["steer", "interrupt"]


def test_phase_two_fork_remains_read_only_and_resume_starts_a_new_turn(
    store: CodexMissionStore, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = FakeClient()
    observer = CodexObserverSupervisor(store, roots=[str(workspace)], client=client)
    mission = observer.start_mission("Inspect", str(workspace))
    fork = observer.fork_mission(mission["id"])
    assert fork["thread_id"] == "thread-2"
    assert client.requests[-1][1]["sandbox"] == "read-only"
    assert client.requests[-1][1]["approvalPolicy"] == "never"

    store.update(mission["id"], status="completed", phase="complete")
    resumed = observer.resume_mission(mission["id"], "Continue with the test findings")
    assert resumed["status"] == "running"
    assert client.requests[-1][0] == "turn/start"
    assert client.requests[-1][1]["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}


def test_observed_error_is_blocked_and_published_as_error(
    store: CodexMissionStore, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = FakeClient()
    bus = EventBus(record_history=True)
    observer = CodexObserverSupervisor(
        store, roots=[str(workspace)], bus=bus, client=client
    )
    mission = observer.start_mission("Inspect", str(workspace))
    client.emit(
        "turn/error",
        {"threadId": "thread-1", "message": "token=abcdefghijklmno"},
    )
    observed = store.get(mission["id"])
    assert observed["status"] == "blocked"
    assert "abcdefghijklmno" not in json.dumps(observed["errors"])
    assert bus.history[-1].event_type == EventType.CODEX_MISSION_ERROR


def test_workspace_scope_cannot_be_widened(
    store: CodexMissionStore, tmp_path: Path
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    observer = CodexObserverSupervisor(store, roots=[str(allowed)], client=FakeClient())
    with pytest.raises(ObserverScopeError):
        observer.start_mission("Inspect", str(outside))


def test_server_requests_are_denied_not_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifications: list[tuple[str, dict[str, Any]]] = []
    replies: list[dict[str, Any]] = []
    client = CodexAppServerClient(
        on_notification=lambda method, params: notifications.append((method, params))
    )
    monkeypatch.setattr(client, "_send", replies.append)
    client._handle_message(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "item/commandExecution/requestApproval",
            "params": {},
        }
    )
    assert replies[0]["error"]["code"] == -32001
    assert client.denied_requests == ["item/commandExecution/requestApproval"]
    assert notifications[0][0] == "observer/requestDenied"


def test_raw_retention_purge_does_not_remove_normalized_evidence(
    store: CodexMissionStore,
) -> None:
    mission_id = store.create_mission("Inspect", "C:/workspace")
    contract, display, _ = normalize("turn/started", {"threadId": "t"})
    store.record_event(
        mission_id, "turn/started", {"secret": "raw"}, contract.to_json(), display
    )
    assert store.purge_expired_raw("9999-01-01T00:00:00+00:00") == 1
    public = store.get(mission_id)
    assert len(public["events"]) == 1
    assert "raw" not in json.dumps(public)


def test_observer_schema_rollback_preserves_unrelated_tables(tmp_path: Path) -> None:
    database = tmp_path / "rollback.db"
    ledger = CodexMissionStore(database)
    ledger.create_mission("Inspect", str(tmp_path))
    ledger.close()
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
    value = connection.execute("SELECT value FROM user_owned").fetchone()[0]
    connection.close()
    assert value == "preserved"
    assert "codex_missions" not in tables
