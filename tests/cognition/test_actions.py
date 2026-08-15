from __future__ import annotations

import sqlite3

import pytest

from openjarvis.cognition import (
    LEGAL_TRANSITIONS,
    ActionError,
    ActionLedger,
    ActionProposal,
    ActionRuntime,
    ActionState,
    Authorization,
    ExecutionOutcome,
    IllegalActionTransition,
)


def _authorized(ledger: ActionLedger, key: str = "key") -> str:
    proposal = ActionProposal(action_type="fake.set", idempotency_key=key)
    ledger.propose(proposal)
    ledger.authorize(Authorization(action_id=proposal.id, authority="test"))
    return proposal.id


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source in ActionState
        for target in ActionState
        if target not in LEGAL_TRANSITIONS[source]
    ],
)
def test_state_machine_rejects_every_illegal_transition(tmp_path, source, target):
    ledger = ActionLedger(tmp_path / f"{source}-{target}.db")
    action_id = _authorized(ledger)
    ledger._conn.execute(
        "UPDATE action_records SET state = ? WHERE action_id = ?",
        (source.value, action_id),
    )
    with pytest.raises(IllegalActionTransition):
        ledger.transition(action_id, target, "property-test")
    ledger.close()


def test_tool_failure_is_failed_and_never_executed(tmp_path):
    ledger = ActionLedger(tmp_path / "actions.db")
    action_id = _authorized(ledger)
    runtime = ActionRuntime(ledger)
    outcome = runtime.execute(
        action_id,
        lambda: ExecutionOutcome(False, "connector rejected", ActionError.PERMANENT),
        executor_name="fake",
    )
    assert not outcome.success
    assert ledger.state(action_id) is ActionState.FAILED
    states = [event["to_state"] for event in ledger.audit(action_id)]
    assert "executed" not in states
    assert "verified" not in states
    assert ledger.attempt_count(action_id) == 1
    ledger.close()


def test_restart_during_execution_becomes_attention_not_retry(tmp_path):
    path = tmp_path / "actions.db"
    first = ActionLedger(path)
    action_id = _authorized(first)
    first.transition(action_id, ActionState.EXECUTING, "crash-injection")
    first.close()

    restarted = ActionLedger(path)
    assert restarted.recover_ambiguous_executions() == 1
    assert restarted.state(action_id) is ActionState.NEEDS_ATTENTION
    restarted.close()


def test_attempt_and_audit_records_are_immutable(tmp_path):
    ledger = ActionLedger(tmp_path / "immutable.db")
    action_id = _authorized(ledger)
    ActionRuntime(ledger).execute(
        action_id,
        lambda: ExecutionOutcome(False, "no change", ActionError.PERMANENT),
        executor_name="fake",
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ledger._conn.execute(
            "DELETE FROM action_attempts WHERE action_id = ?", (action_id,)
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ledger._conn.execute("UPDATE action_audit SET reason = 'rewritten'")
    ledger.close()
