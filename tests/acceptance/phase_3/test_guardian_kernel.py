"""Phase 3 tangible scenario: Guardian-approved effects and truthful recovery."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.cognition import (
    ActionError,
    ActionLedger,
    ActionProposal,
    ActionState,
    ExecutionOutcome,
)
from openjarvis.guardian import (
    ActionDefinition,
    ActionRegistry,
    GuardianKernel,
    PreconditionResult,
)


def _definition(action_type, world, *, verifier_ok=True, preconditions=None):
    return ActionDefinition(
        action_type=action_type,
        input_schema={
            "type": "object",
            "required": ["target", "value"],
            "properties": {"target": {"type": "string"}, "value": {"type": "boolean"}},
            "additionalProperties": False,
        },
        capability="workspace.write"
        if action_type.startswith("codex.")
        else "home.write",
        risk_class="reversible",
        consequence_class="low",
        preconditions=preconditions
        or (
            lambda _: [
                PreconditionResult("fixture-fresh", True, datetime.now(timezone.utc))
            ]
        ),
        executor=lambda proposal: _set(world, proposal),
        verifier=lambda proposal: (
            verifier_ok
            and world[proposal.parameters["target"]] == proposal.parameters["value"],
            {proposal.parameters["target"]: world[proposal.parameters["target"]]},
        ),
        expected_effect={"state": "set"},
    )


def _set(world, proposal):
    world[proposal.parameters["target"]] = proposal.parameters["value"]
    world["effects"] += 1
    return ExecutionOutcome(True, {"reported": "success"})


def _kernel(tmp_path, definition):
    ledger = ActionLedger(tmp_path / "guardian.db")
    registry = ActionRegistry()
    registry.register(definition)
    kernel = GuardianKernel(ledger, registry)
    kernel.grant(
        grant_id="grant-1",
        session_id="session-1",
        capability=definition.capability,
        scope={"action_type": definition.action_type, "target": "target"},
    )
    return ledger, kernel


def _proposal(action_type, key):
    return ActionProposal(
        action_type=action_type,
        parameters={"target": "target", "value": True},
        idempotency_key=key,
    )


def test_phase_3_tangible_scenario(tmp_path):
    world = {"target": False, "effects": 0}
    ledger, kernel = _kernel(tmp_path, _definition("home.state.set", world))

    approved = _proposal("home.state.set", "approved")
    decision = kernel.authorize(approved, session_id="session-1", authority="Marc")
    result = kernel.execute(approved.id, decision.authorization)
    assert result.state is ActionState.VERIFIED
    assert result.verification and result.verification.succeeded

    denied = _proposal("home.state.set", "denied")
    kernel.revoke("session-1")
    denied_decision = kernel.authorize(denied, session_id="session-1", authority="Marc")
    assert not denied_decision.allowed
    assert ledger.state(denied.id) is ActionState.FAILED

    # A tool success is never accepted as proof when the read path disagrees.
    kernel.grant(
        grant_id="grant-2",
        session_id="session-2",
        capability="workspace.write",
        scope={"action_type": "codex.workspace.set", "target": "target"},
    )
    failing_definition = _definition("codex.workspace.set", world, verifier_ok=False)
    kernel.registry.register(failing_definition)
    unverified = _proposal("codex.workspace.set", "unverified")
    unverified_decision = kernel.authorize(
        unverified, session_id="session-2", authority="Marc"
    )
    unverified_result = kernel.execute(unverified.id, unverified_decision.authorization)
    assert unverified_result.state is ActionState.FAILED
    assert (
        unverified_result.verification and not unverified_result.verification.succeeded
    )

    # A transport timeout after dispatch is ambiguous: no automatic duplicate retry.
    def timeout_after_effect(_):
        world["effects"] += 1
        return ExecutionOutcome(False, "connection lost", ActionError.AMBIGUOUS_EFFECT)

    timeout_definition = ActionDefinition(
        **{
            **_definition("home.timeout", world).__dict__,
            "action_type": "home.timeout",
            "executor": timeout_after_effect,
        }
    )
    kernel.registry.register(timeout_definition)
    kernel.grant(
        grant_id="grant-3",
        session_id="session-3",
        capability="home.write",
        scope={"action_type": "home.timeout", "target": "target"},
    )
    timeout = _proposal("home.timeout", "timeout")
    timeout_decision = kernel.authorize(
        timeout, session_id="session-3", authority="Marc"
    )
    timeout_result = kernel.execute(timeout.id, timeout_decision.authorization)
    assert timeout_result.state is ActionState.NEEDS_ATTENTION
    assert ledger.attempt_count(timeout.id) == 1
    assert world["effects"] == 3

    chain = kernel.timeline(approved.id)
    assert chain["state"] == "verified"
    assert chain["attempts"] and chain["verifications"] and chain["guardian_audit"]
    kernel.close()
    ledger.close()


def test_unknown_stale_contradictory_and_revoked_actions_fail_closed(tmp_path):
    world = {"target": False, "effects": 0}

    def stale_or_contradictory(proposal):
        contradictory = proposal.idempotency_key == "contradictory"
        missing = proposal.idempotency_key == "missing"
        return [
            PreconditionResult(
                "fixture",
                not missing,
                datetime.now(timezone.utc)
                - (timedelta(minutes=2) if not contradictory else timedelta()),
                contradictory=contradictory,
            )
        ]

    ledger, kernel = _kernel(
        tmp_path,
        _definition("home.state.set", world, preconditions=stale_or_contradictory),
    )

    unknown = _proposal("unknown.effect", "unknown")
    unknown_decision = kernel.authorize(
        unknown, session_id="session-1", authority="Marc"
    )
    assert not unknown_decision.allowed
    assert ledger.state(unknown.id) is ActionState.FAILED

    blocked = _proposal("home.state.set", "stale")
    blocked_decision = kernel.authorize(
        blocked, session_id="session-1", authority="Marc"
    )
    assert (
        kernel.execute(blocked.id, blocked_decision.authorization).state
        is ActionState.FAILED
    )
    assert world["effects"] == 0

    missing = _proposal("home.state.set", "missing")
    missing_decision = kernel.authorize(
        missing, session_id="session-1", authority="Marc"
    )
    assert (
        kernel.execute(missing.id, missing_decision.authorization).state
        is ActionState.FAILED
    )
    assert world["effects"] == 0

    contradictory = _proposal("home.state.set", "contradictory")
    contradictory_decision = kernel.authorize(
        contradictory, session_id="session-1", authority="Marc"
    )
    assert (
        kernel.execute(contradictory.id, contradictory_decision.authorization).state
        is ActionState.FAILED
    )
    assert world["effects"] == 0

    # Revocation is checked at execution time, after approval but before effect.
    fresh_ledger, fresh_kernel = _kernel(
        tmp_path / "fresh", _definition("home.state.set", world)
    )
    pending = _proposal("home.state.set", "revoked")
    pending_decision = fresh_kernel.authorize(
        pending, session_id="session-1", authority="Marc"
    )
    fresh_kernel.revoke("session-1")
    assert (
        fresh_kernel.execute(pending.id, pending_decision.authorization).state
        is ActionState.FAILED
    )
    assert world["effects"] == 0
    fresh_kernel.close()
    fresh_ledger.close()
    kernel.close()
    ledger.close()


def test_denied_action_cannot_be_rephrased_and_emergency_stop_outranks_grant(tmp_path):
    world = {"target": False, "effects": 0}
    ledger, kernel = _kernel(tmp_path, _definition("home.state.set", world))
    kernel.revoke("session-1")
    first = _proposal("home.state.set", "first-denial")
    assert not kernel.authorize(first, session_id="session-1", authority="Marc").allowed
    kernel.grant(
        grant_id="grant-2",
        session_id="session-2",
        capability="home.write",
        scope={"action_type": "home.state.set", "target": "target"},
    )
    equivalent = _proposal("home.state.set", "equivalent-denial")
    assert not kernel.authorize(
        equivalent, session_id="session-2", authority="Marc"
    ).allowed
    kernel.emergency_stop(reason="fixture stop")
    different = ActionProposal(
        action_type="home.state.set",
        parameters={"target": "target", "value": False},
        idempotency_key="stop",
    )
    assert not kernel.authorize(
        different, session_id="session-2", authority="Marc"
    ).allowed
    assert world["effects"] == 0
    kernel.close()
    ledger.close()


def test_duplicate_delivery_is_idempotent_and_proposal_collisions_fail_closed(
    tmp_path,
):
    world = {"target": False, "effects": 0}
    ledger, kernel = _kernel(tmp_path, _definition("home.state.set", world))

    proposal = _proposal("home.state.set", "duplicate")
    decision = kernel.authorize(proposal, session_id="session-1", authority="Marc")
    assert decision.allowed
    replay_before_execute = kernel.authorize(
        proposal, session_id="session-1", authority="Marc"
    )
    assert replay_before_execute.allowed
    assert (
        kernel.execute(proposal.id, replay_before_execute.authorization).state
        is ActionState.VERIFIED
    )

    replay_after_execute = kernel.authorize(
        ActionProposal.from_dict(proposal.to_dict()),
        session_id="session-1",
        authority="Marc",
    )
    assert not replay_after_execute.allowed
    assert "already handled" in replay_after_execute.reason
    assert world["effects"] == 1

    collision = ActionProposal(
        id=proposal.id,
        action_type=proposal.action_type,
        parameters={"target": "target", "value": False},
        idempotency_key=proposal.idempotency_key,
    )
    collision_decision = kernel.authorize(
        collision, session_id="session-1", authority="Marc"
    )
    assert not collision_decision.allowed
    assert "conflicts" in collision_decision.reason
    assert world["effects"] == 1

    kernel.close()
    ledger.close()


def test_json_number_schema_rejects_boolean_values(tmp_path):
    registry = ActionRegistry()
    definition = _definition("fixture.number", {"target": False, "effects": 0})
    definition = ActionDefinition(
        **{
            **definition.__dict__,
            "action_type": "fixture.number",
            "input_schema": {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "number"}},
                "additionalProperties": False,
            },
        }
    )
    registry.register(definition)
    proposal = ActionProposal(
        action_type="fixture.number",
        parameters={"value": True},
        idempotency_key="number-bool",
    )
    assert registry.validate(proposal) == "action parameter 'value' must be number"


def test_precondition_and_executor_failures_are_recorded_without_side_effect(
    tmp_path,
):
    world = {"target": False, "effects": 0}
    failing_precondition = _definition(
        "fixture.precondition-error",
        world,
        preconditions=lambda _: (_ for _ in ()).throw(RuntimeError("fixture")),
    )
    ledger, kernel = _kernel(tmp_path / "precondition", failing_precondition)
    proposal = _proposal("fixture.precondition-error", "precondition-error")
    decision = kernel.authorize(proposal, session_id="session-1", authority="Marc")
    result = kernel.execute(proposal.id, decision.authorization)
    assert result.state is ActionState.FAILED
    assert result.outcome.error is ActionError.STALE
    assert ledger.attempt_count(proposal.id) == 0
    assert world["effects"] == 0
    kernel.close()
    ledger.close()

    def timeout_executor(_):
        raise TimeoutError("fixture timeout")

    invalid_executor = ActionDefinition(
        **{
            **_definition("fixture.executor-error", world).__dict__,
            "action_type": "fixture.executor-error",
            "executor": lambda _: None,
        }
    )
    ledger, kernel = _kernel(tmp_path / "executor", invalid_executor)
    proposal = _proposal("fixture.executor-error", "executor-error")
    decision = kernel.authorize(proposal, session_id="session-1", authority="Marc")
    result = kernel.execute(proposal.id, decision.authorization)
    assert result.state is ActionState.FAILED
    assert result.outcome.error is ActionError.INVALID
    assert ledger.attempt_count(proposal.id) == 1
    assert world["effects"] == 0
    kernel.close()
    ledger.close()

    timeout_definition = ActionDefinition(
        **{
            **_definition("fixture.timeout-error", world).__dict__,
            "action_type": "fixture.timeout-error",
            "executor": timeout_executor,
        }
    )
    ledger, kernel = _kernel(tmp_path / "timeout", timeout_definition)
    proposal = _proposal("fixture.timeout-error", "timeout-error")
    decision = kernel.authorize(proposal, session_id="session-1", authority="Marc")
    result = kernel.execute(proposal.id, decision.authorization)
    assert result.state is ActionState.NEEDS_ATTENTION
    assert result.outcome.error is ActionError.AMBIGUOUS_EFFECT
    assert ledger.attempt_count(proposal.id) == 1
    assert world["effects"] == 0
    kernel.close()
    ledger.close()


def test_guardian_grants_stop_and_audit_survive_restart(tmp_path):
    world = {"target": False, "effects": 0}
    database = tmp_path / "guardian.db"
    definition = _definition("home.state.set", world)

    first_ledger = ActionLedger(database)
    first_registry = ActionRegistry()
    first_registry.register(definition)
    first_kernel = GuardianKernel(first_ledger, first_registry)
    first_kernel.grant(
        grant_id="persistent-grant",
        session_id="persistent-session",
        capability="home.write",
        scope={"action_type": "home.state.set", "target": "target"},
    )
    completed = _proposal("home.state.set", "persistent-completed")
    decision = first_kernel.authorize(
        completed, session_id="persistent-session", authority="Marc"
    )
    assert (
        first_kernel.execute(completed.id, decision.authorization).state
        is ActionState.VERIFIED
    )
    first_kernel.revoke("persistent-session")
    first_kernel.emergency_stop(reason="restart fixture stop")
    first_kernel.close()
    first_ledger.close()

    second_ledger = ActionLedger(database)
    second_registry = ActionRegistry()
    second_registry.register(definition)
    second_kernel = GuardianKernel(second_ledger, second_registry)

    timeline = second_kernel.timeline(completed.id)
    assert timeline["state"] == ActionState.VERIFIED.value
    assert {item["event"] for item in timeline["guardian_audit"]} >= {
        "authorization-granted",
        "verification-finished",
    }
    persisted_events = {
        row[0]
        for row in second_kernel._conn.execute(
            "SELECT event FROM guardian_audit"
        ).fetchall()
    }
    assert persisted_events >= {
        "grant-created",
        "session-revoked",
        "emergency-stop",
    }

    stopped = _proposal("home.state.set", "restart-stop-denial")
    stopped_decision = second_kernel.authorize(
        stopped, session_id="persistent-session", authority="Marc"
    )
    assert not stopped_decision.allowed
    assert second_ledger.state(stopped.id) is ActionState.FAILED
    assert second_ledger.attempt_count(stopped.id) == 0
    assert world["effects"] == 1

    with pytest.raises(PermissionError):
        second_kernel.clear_emergency_stop(authority="not-Marc")
    second_kernel.clear_emergency_stop(authority="Marc")
    revoked = _proposal("home.state.set", "restart-revoked-denial")
    revoked_decision = second_kernel.authorize(
        revoked, session_id="persistent-session", authority="Marc"
    )
    assert not revoked_decision.allowed
    assert second_ledger.state(revoked.id) is ActionState.FAILED
    assert world["effects"] == 1

    with sqlite3.connect(database) as raw:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("UPDATE guardian_audit SET event='tampered' WHERE sequence=1")
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("DELETE FROM guardian_audit WHERE sequence=1")

    second_kernel.close()
    second_ledger.close()
