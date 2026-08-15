"""Regression coverage for the legacy false-`executed` proactive path."""

from openjarvis.tools.approval_store import (
    STATUS_APPROVED,
    STATUS_EFFECT_PENDING,
    STATUS_FAILED,
    ApprovalStore,
)
from openjarvis.tools.proactive_tools import ExecutePendingActionsTool


def _approved(store: ApprovalStore, description: str) -> str:
    action = store.queue_action(
        action_type="fake",
        description=description,
        payload={},
        permission_key=f"fake:{description}",
        tier="trivial",
    )
    store.update_status(action.id, STATUS_APPROVED)
    return action.id


def test_proactive_tool_never_marks_reported_failure_executed(tmp_path):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    action_id = _approved(store, "forced failure")
    result = ExecutePendingActionsTool(
        store=store, executor_fn=lambda _: (False, "adapter rejected")
    ).execute(action_ids=[action_id])

    assert result.success
    assert store.get_action(action_id).status == STATUS_FAILED
    states = [event["to_state"] for event in store.ledger.audit(action_id)]
    assert states[-1] == "failed"
    assert "executed" not in states
    assert "verified" not in states
    store.close()


def test_proactive_tool_success_waits_for_independent_verification(tmp_path):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    action_id = _approved(store, "reported success")
    ExecutePendingActionsTool(
        store=store, executor_fn=lambda _: (True, "adapter accepted")
    ).execute(action_ids=[action_id])

    assert store.get_action(action_id).status == STATUS_EFFECT_PENDING
    assert store.ledger.state(action_id).value == "effect_pending"
    store.close()
