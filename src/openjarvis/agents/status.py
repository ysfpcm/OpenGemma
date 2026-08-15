"""Derived managed-agent run status for operators and the Operations UI."""

from __future__ import annotations

from typing import Any, Iterable


def _latest_agent_message(messages: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the newest agent response from a newest-first message list."""
    return next(
        (
            message
            for message in messages
            if message.get("direction") == "agent_to_user"
        ),
        None,
    )


def _tool_result(tool_call: dict[str, Any] | None) -> dict[str, Any] | None:
    if not tool_call:
        return None
    return {
        "tool": tool_call.get("tool", ""),
        "success": tool_call.get("success"),
        "result": str(tool_call.get("result", ""))[:1000],
        "latency": tool_call.get("latency"),
    }


def _delivery_result(tool_calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Extract the last notification attempt without conflating it with a run."""
    for tool_call in reversed(tool_calls):
        tool = tool_call.get("tool")
        metadata = tool_call.get("metadata") or {}
        if tool == "channel_send":
            sent = tool_call.get("success") is True
            return {
                "status": "sent" if sent else "failed",
                "tool": tool,
                "detail": str(tool_call.get("result", ""))[:1000],
            }
        if tool == "commute_readiness":
            test_send_status = metadata.get("test_send_status")
            if test_send_status in {"sent", "failed"}:
                return {
                    "status": test_send_status,
                    "tool": tool,
                    "detail": str(tool_call.get("result", ""))[:1000],
                }
    return None


def build_agent_run_status(
    agent: dict[str, Any],
    *,
    messages: Iterable[dict[str, Any]] = (),
    schedule: dict[str, Any] | None = None,
    latest_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable, UI-friendly run status snapshot.

    ``run_outcome`` describes whether the agent completed its turn. The
    separate ``delivery_result`` describes whether an external notification
    actually succeeded; keeping them separate prevents a successful model turn
    from being presented as a delivered SMS.
    """
    message = _latest_agent_message(messages)
    tool_calls = list((message or {}).get("tool_calls") or [])
    last_tool = _tool_result(tool_calls[-1] if tool_calls else None)
    delivery = _delivery_result(tool_calls)
    failed_tool = next(
        (
            tool_call
            for tool_call in reversed(tool_calls)
            if tool_call.get("success") is False
        ),
        None,
    )

    run_outcome = (latest_trace or {}).get("outcome")
    if run_outcome is None and message is not None:
        run_outcome = "success"

    failure_reason: str | None = None
    if delivery and delivery["status"] == "failed":
        failure_reason = delivery["detail"] or "Notification delivery failed."
    elif failed_tool is not None:
        failure_reason = (
            str(failed_tool.get("result", ""))[:1000]
            or f"Tool {failed_tool.get('tool', 'call')} failed."
        )
    elif last_tool and last_tool["success"] is False:
        failure_reason = last_tool["result"] or "The latest tool call failed."
    elif agent.get("status") in {"error", "needs_attention", "stalled"}:
        summary = str(agent.get("summary_memory") or "")
        failure_reason = (
            summary.removeprefix("ERROR: ").strip() or "Agent needs attention."
        )

    if delivery and delivery["status"] == "failed":
        overall_status = "action_failed"
    elif failed_tool is not None:
        overall_status = "action_failed"
    elif run_outcome == "error" or agent.get("status") in {"error", "needs_attention"}:
        overall_status = "error"
    elif agent.get("status") == "running":
        overall_status = "running"
    else:
        overall_status = "ok"

    return {
        "id": agent["id"],
        "name": agent["name"],
        "status": agent.get("status", "idle"),
        "overall_status": overall_status,
        "current_activity": agent.get("current_activity", ""),
        "last_run_at": agent.get("last_run_at"),
        "last_outcome": run_outcome,
        "next_run_at": (schedule or {}).get("next_run_at"),
        "schedule_type": (schedule or {}).get("schedule_type", "manual"),
        "schedule_value": (schedule or {}).get("schedule_value", ""),
        "last_tool_result": last_tool,
        "delivery_result": delivery,
        "failure_reason": failure_reason,
    }


__all__ = ["build_agent_run_status"]
