"""End-to-end contracts for scheduled agent actions and operator status."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import patch

from openjarvis.agents._stubs import AgentResult
from openjarvis.agents.executor import AgentExecutor
from openjarvis.agents.manager import AgentManager
from openjarvis.agents.status import build_agent_run_status
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.commute_tool import CommuteReadinessTool
from openjarvis.tools.traffic_tool import TrafficTool


def _live_traffic(monkeypatch, *, traffic_minutes: int = 20) -> None:
    def fake_traffic(self: TrafficTool, **params: Any) -> ToolResult:
        return ToolResult(
            tool_name="traffic_lookup",
            content="live structured result",
            success=True,
            metadata={
                "provider": "google_maps",
                "normal_duration_seconds": 20 * 60,
                "traffic_duration_seconds": traffic_minutes * 60,
            },
        )

    monkeypatch.setattr(TrafficTool, "execute", fake_traffic)


def test_outside_window_skips_traffic_and_notification(monkeypatch):
    def fail_if_called(self: TrafficTool, **params: Any) -> ToolResult:
        raise AssertionError("traffic must not run outside the operating window")

    monkeypatch.setattr(TrafficTool, "execute", fail_if_called)
    result = CommuteReadinessTool().execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        current_time="2026-08-09T15:53:00",
    )

    assert result.success is False
    assert result.metadata["reason"] == "outside_operating_window"
    assert "Do not send an alert" in result.content


def test_tool_timeout_is_a_failed_non_actionable_result():
    class SlowTool(BaseTool):
        tool_id = "slow_contract_tool"

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name=self.tool_id,
                description="Sleeps past its contract timeout.",
                timeout_seconds=0.01,
            )

        def execute(self, **params: Any) -> ToolResult:
            time.sleep(0.05)
            return ToolResult(tool_name=self.tool_id, content="late", success=True)

    bus = EventBus(record_history=True)
    result = ToolExecutor([SlowTool()], bus=bus).execute(
        ToolCall(
            id="contract-timeout",
            name="slow_contract_tool",
            arguments=json.dumps({}),
        )
    )

    assert result.success is False
    assert "timed out" in result.content
    assert any(event.event_type == EventType.TOOL_TIMEOUT for event in bus.history)


def test_traffic_unavailability_never_becomes_a_safe_commute_result(monkeypatch):
    def unavailable(self: TrafficTool, **params: Any) -> ToolResult:
        return ToolResult(
            tool_name="traffic_lookup",
            content="provider timed out",
            success=False,
        )

    monkeypatch.setattr(TrafficTool, "execute", unavailable)
    result = CommuteReadinessTool().execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        current_time="2026-08-10T05:00:00",
    )

    assert result.success is False
    assert result.metadata["provider_confidence"] == "unavailable"
    assert "Do not use this result" in result.content


def test_channel_delivery_failure_is_reported_truthfully(monkeypatch):
    _live_traffic(monkeypatch)

    class FailingChannel:
        def send(self, target: str, content: str, conversation_id: str = "") -> bool:
            return False

    tool = CommuteReadinessTool()
    tool._channel = FailingChannel()
    result = tool.execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        current_time="2026-08-09T15:53:00",
        test_mode=True,
        send_test_to="+15551234567",
    )

    assert result.success is False
    assert result.metadata["test_send_status"] == "failed"
    assert "delivery failed" in result.content.lower()
    assert "Test SendBlue delivery: sent" not in result.content


def test_test_mode_sends_exactly_one_labeled_notification(monkeypatch):
    _live_traffic(monkeypatch)
    sent: list[tuple[str, str]] = []

    class RecordingChannel:
        def send(self, target: str, content: str, conversation_id: str = "") -> bool:
            sent.append((target, content))
            return True

    tool = CommuteReadinessTool()
    tool._channel = RecordingChannel()
    result = tool.execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        current_time="2026-08-09T15:53:00",
        test_mode=True,
        send_test_to="+15551234567",
    )

    assert result.success is True
    assert result.metadata["test_send_status"] == "sent"
    assert len(sent) == 1
    assert sent[0][0] == "+15551234567"
    assert sent[0][1].startswith("TEST ONLY")


def test_time_only_deadline_uses_the_next_calendar_occurrence(monkeypatch):
    _live_traffic(monkeypatch)
    result = CommuteReadinessTool().execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        buffer_minutes=15,
        current_time="2026-08-09T15:53:00",
        test_mode=True,
    )

    assert result.success is True
    assert result.metadata["arrival_time"] == "2026-08-10T06:30:00"
    assert result.metadata["leave_by"] == "2026-08-10T05:55:00"
    assert "Arrival target: 6:30 AM" in result.content
    assert "3:53 PM" in result.content


def test_operations_status_separates_run_success_from_delivery_failure():
    agent = {
        "id": "agent-1",
        "name": "Commute Readiness",
        "status": "idle",
        "current_activity": "",
        "last_run_at": 100.0,
        "summary_memory": "",
    }
    status = build_agent_run_status(
        agent,
        messages=[
            {
                "direction": "agent_to_user",
                "tool_calls": [
                    {
                        "tool": "channel_send",
                        "success": False,
                        "result": "No channel backend configured.",
                        "latency": 0.01,
                    }
                ],
            }
        ],
        schedule={
            "schedule_type": "cron",
            "schedule_value": "0 5 * * *",
            "next_run_at": 200.0,
        },
        latest_trace={"outcome": "success"},
    )

    assert status["last_outcome"] == "success"
    assert status["overall_status"] == "action_failed"
    assert status["delivery_result"]["status"] == "failed"
    assert "No channel backend" in status["failure_reason"]


def test_executor_persists_tool_outcome_for_operations_status(tmp_path):
    manager = AgentManager(str(tmp_path / "agents.db"))
    bus = EventBus()
    executor = AgentExecutor(manager, bus)
    agent = manager.create_agent("status-contract")

    def fake_invoke(agent_dict: dict[str, Any]) -> AgentResult:
        bus.publish(
            EventType.TOOL_CALL_START,
            {
                "agent": agent_dict["id"],
                "tool": "channel_send",
                "arguments": {"channel": "sms"},
            },
        )
        bus.publish(
            EventType.TOOL_CALL_END,
            {
                "agent": agent_dict["id"],
                "tool": "channel_send",
                "success": False,
                "result": "No channel backend configured.",
                "latency": 0.01,
            },
        )
        return AgentResult(content="The notification was not delivered.")

    with patch.object(executor, "_invoke_agent", side_effect=fake_invoke):
        executor.execute_tick(agent["id"])

    messages = manager.list_messages(agent["id"])
    assert messages[0]["tool_calls"][0]["success"] is False
    assert messages[0]["tool_calls"][0]["result"] == "No channel backend configured."
    manager.close()
