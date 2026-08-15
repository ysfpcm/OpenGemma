"""Phase 0 tangible demonstration: success, failure, restart, and duplicate."""

from __future__ import annotations

from openjarvis.cognition import (
    ActionError,
    ActionLedger,
    ActionProposal,
    ActionRuntime,
    ActionState,
    Authorization,
    ExecutionOutcome,
)


def test_phase_0_tangible_scenario(tmp_path):
    database = tmp_path / "ophanim.db"
    world = {"a": False, "side_effect_count": 0}

    ledger = ActionLedger(database)
    action_a = ActionProposal(
        action_type="fake.set",
        description="Set A true",
        parameters={"target": "a", "value": True},
        idempotency_key="phase-0/action-a",
    )
    action_b = ActionProposal(
        action_type="fake.timeout",
        description="Action B times out without changing state",
        idempotency_key="phase-0/action-b",
    )
    assert ledger.propose(action_a) == (action_a.id, True)
    assert ledger.propose(action_b) == (action_b.id, True)
    ledger.authorize(Authorization(action_id=action_a.id, authority="phase-0-test"))
    ledger.authorize(Authorization(action_id=action_b.id, authority="phase-0-test"))

    runtime = ActionRuntime(ledger)

    def execute_a():
        world["a"] = True
        world["side_effect_count"] += 1
        return ExecutionOutcome(True, {"reported": "success"})

    runtime.execute(action_a.id, execute_a, executor_name="fake-a")
    runtime.execute(
        action_b.id,
        lambda: ExecutionOutcome(False, "timed out", ActionError.TRANSIENT),
        executor_name="fake-b",
    )
    assert ledger.state(action_a.id) is ActionState.EFFECT_PENDING
    assert ledger.state(action_b.id) is ActionState.FAILED
    ledger.close()  # injected restart between attempt and verification

    restarted = ActionLedger(database)
    assert restarted.recovery_queue() == [action_a.id]

    duplicate_id, created = restarted.propose(
        ActionProposal(
            action_type="fake.set",
            parameters={"target": "a", "value": True},
            idempotency_key="phase-0/action-a",
        )
    )
    assert duplicate_id == action_a.id
    assert created is False
    duplicate_outcome = ActionRuntime(restarted).execute(
        duplicate_id, execute_a, executor_name="fake-a"
    )
    assert duplicate_outcome.success is False
    assert world["side_effect_count"] == 1

    verification = ActionRuntime(restarted).verify(
        action_a.id,
        lambda: (world["a"] is True, {"a": world["a"]}),
        verifier_name="independent-world-read",
    )
    assert verification.succeeded
    assert restarted.state(action_a.id) is ActionState.VERIFIED
    assert restarted.state(action_b.id) is ActionState.FAILED

    a_states = [event["to_state"] for event in restarted.audit(action_a.id)]
    b_states = [event["to_state"] for event in restarted.audit(action_b.id)]
    assert a_states == [
        "proposed",
        "authorized",
        "executing",
        "effect_pending",
        "verified",
    ]
    assert b_states == ["proposed", "authorized", "executing", "failed"]
    assert "executed" not in a_states + b_states
    restarted.close()
