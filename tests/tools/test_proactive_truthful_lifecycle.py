from __future__ import annotations

import json

from openjarvis.tools.approval_store import (
    STATUS_EFFECT_PENDING,
    STATUS_FAILED,
    TIER_MEDIUM,
    ApprovalStore,
)
from openjarvis.tools.proactive_tools import ExecutePendingActionsTool


def _approved(store: ApprovalStore, description: str) -> str:
    action = store.queue_action(
        action_type="fake",
        description=description,
        payload={},
        permission_key=f"fake:{description}",
        tier=TIER_MEDIUM,
    )
    store.update_status(action.id, "approved")
    return action.id


def test_proactive_executor_no_longer_marks_failures_executed(tmp_path):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    success_id = _approved(store, "success")
    failure_id = _approved(store, "failure")

    tool = ExecutePendingActionsTool(
        store=store,
        executor_fn=lambda action: (
            (True, "tool accepted")
            if action.id == success_id
            else (False, "tool failed")
        ),
    )
    results = json.loads(tool.execute().content)
    by_id = {item["id"]: item for item in results}

    assert by_id[success_id]["lifecycle_state"] == STATUS_EFFECT_PENDING
    assert by_id[failure_id]["lifecycle_state"] == STATUS_FAILED
    assert store.get_action(success_id).status == STATUS_EFFECT_PENDING
    assert store.get_action(failure_id).status == STATUS_FAILED
    assert store.ledger.attempt_count(success_id) == 1
    assert store.ledger.attempt_count(failure_id) == 1

    # Neither action remains eligible for a second execution.
    assert json.loads(tool.execute().content) == []
    store.close()
