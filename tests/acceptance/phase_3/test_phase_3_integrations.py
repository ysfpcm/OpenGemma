"""Phase 3 integration proof for native Codex and Home Assistant adapters."""

from __future__ import annotations

import httpx
from tests.codex_observer.test_phase_2_mission_control import FixtureCodexClient

from openjarvis.codex_observer.store import CodexMissionStore
from openjarvis.codex_observer.supervisor import CodexObserverSupervisor
from openjarvis.cognition import ActionLedger, ActionProposal, ActionState
from openjarvis.guardian import (
    ActionRegistry,
    CodexGuardianBridge,
    GuardianKernel,
    register_home_assistant_actions,
)
from openjarvis.tools.home_assistant import HomeAssistantTool


def test_codex_native_approval_gets_exact_guardian_authorization(tmp_path):
    ledger = ActionLedger(tmp_path / "guardian.db")
    kernel = GuardianKernel(ledger, ActionRegistry())
    bridge = CodexGuardianBridge(kernel)

    approved = bridge.authorize(
        mission_id="mission-1",
        decision_id="decision-1",
        request_id=7,
        kind="command",
        workspace="C:/fixture",
        requested={"command": "pytest -q", "cwd": "C:/fixture"},
    )
    assert approved.allowed
    assert ledger.state(approved.authorization.action_id) is ActionState.AUTHORIZED
    timeline = kernel.timeline(approved.authorization.action_id)
    assert timeline["proposal"]["parameters"]["request_digest"]
    assert timeline["state"] == "authorized"

    denied = bridge.deny(
        mission_id="mission-1",
        decision_id="decision-2",
        request_id=8,
        kind="network",
        workspace="C:/fixture",
        requested={"command": "curl example.test"},
    )
    assert not denied.allowed
    assert ledger.state(denied.authorization.action_id) is ActionState.FAILED
    kernel.close()
    ledger.close()


def test_codex_supervisor_records_guardian_authorization_before_native_reply(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    store = CodexMissionStore(tmp_path / "missions.db")
    ledger = ActionLedger(tmp_path / "guardian.db")
    kernel = GuardianKernel(ledger, ActionRegistry())
    client = FixtureCodexClient()
    supervisor = CodexObserverSupervisor(
        store,
        roots=[str(tmp_path)],
        client=client,
        guardian_bridge=CodexGuardianBridge(kernel),
    )
    mission = supervisor.start_mission(
        "Run a fixture test", str(root), mode="workspace-write"
    )
    client.request_from_codex(
        9,
        "item/commandExecution/requestApproval",
        {
            "threadId": mission["thread_id"],
            "turnId": mission["active_turn_id"],
            "itemId": "command-1",
            "command": "pytest -q",
            "cwd": str(root),
        },
    )
    decision = store.list_decisions(mission["id"])[0]
    assert decision["status"] == "pending"
    supervisor.resolve_decision(decision["id"], {"decision": "accept"})

    controls = store.get(mission["id"])["controls"]
    action_id = controls[-1]["detail"]["guardian_action_id"]
    assert kernel.timeline(action_id)["state"] == "authorized"
    assert client.responses[-1][1]["result"] == {"decision": "accept"}
    kernel.close()
    ledger.close()
    store.close()


def test_registered_home_action_executes_and_verifies_independently(
    tmp_path, monkeypatch
):
    tool = HomeAssistantTool()
    state_reads = iter(
        [
            {"entity_id": "light.fixture", "state": "on", "attributes": {}},
            {"entity_id": "light.fixture", "state": "off", "attributes": {}},
        ]
    )
    calls = []
    monkeypatch.setattr(tool, "_request", lambda *_: next(state_reads))
    monkeypatch.setattr(
        tool,
        "_call_service",
        lambda domain, service, entity_id: calls.append((domain, service, entity_id)),
    )
    ledger = ActionLedger(tmp_path / "guardian.db")
    registry = ActionRegistry()
    register_home_assistant_actions(registry, tool)
    kernel = GuardianKernel(ledger, registry)
    kernel.grant(
        grant_id="home-grant",
        session_id="home-session",
        capability="home.assistant.write",
        scope={"action_type": "home_assistant.turn_off", "target": "light.fixture"},
    )
    proposal = ActionProposal(
        action_type="home_assistant.turn_off",
        parameters={"target": "light.fixture"},
        idempotency_key="home-fixture-off",
    )
    decision = kernel.authorize(proposal, session_id="home-session", authority="Marc")
    result = kernel.execute(proposal.id, decision.authorization)

    assert result.state is ActionState.VERIFIED
    assert calls == [("light", "turn_off", "light.fixture")]
    assert result.verification and result.verification.observed_effect["state"] == "off"
    kernel.close()
    ledger.close()


def test_registered_home_timeout_is_ambiguous_and_never_retried(tmp_path, monkeypatch):
    tool = HomeAssistantTool()
    monkeypatch.setattr(
        tool,
        "_request",
        lambda *_: {"entity_id": "light.fixture", "state": "on", "attributes": {}},
    )
    monkeypatch.setattr(
        tool,
        "_call_service",
        lambda *_: (_ for _ in ()).throw(httpx.ReadTimeout("fixture timeout")),
    )
    ledger = ActionLedger(tmp_path / "guardian.db")
    registry = ActionRegistry()
    register_home_assistant_actions(registry, tool)
    kernel = GuardianKernel(ledger, registry)
    kernel.grant(
        grant_id="home-timeout-grant",
        session_id="home-timeout-session",
        capability="home.assistant.write",
        scope={"action_type": "home_assistant.turn_off", "target": "light.fixture"},
    )
    proposal = ActionProposal(
        action_type="home_assistant.turn_off",
        parameters={"target": "light.fixture"},
        idempotency_key="home-fixture-timeout",
    )
    decision = kernel.authorize(
        proposal, session_id="home-timeout-session", authority="Marc"
    )
    result = kernel.execute(proposal.id, decision.authorization)

    assert result.state is ActionState.NEEDS_ATTENTION
    assert ledger.attempt_count(proposal.id) == 1
    kernel.close()
    ledger.close()
