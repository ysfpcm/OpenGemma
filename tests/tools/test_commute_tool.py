"""Tests for deterministic commute readiness calculations."""

from __future__ import annotations

from openjarvis.core.types import ToolResult
from openjarvis.tools.commute_tool import CommuteReadinessTool
from openjarvis.tools.traffic_tool import TrafficTool


def test_commute_readiness_calculates_leave_by_and_risk(monkeypatch):
    def fake_traffic(self, **params):
        return ToolResult(
            tool_name="traffic_lookup",
            content="live",
            success=True,
            metadata={
                "provider": "google_maps",
                "normal_duration_seconds": 20 * 60,
                "traffic_duration_seconds": 30 * 60,
            },
        )

    monkeypatch.setattr(TrafficTool, "execute", fake_traffic)
    result = CommuteReadinessTool().execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        buffer_minutes=15,
        current_time="2026-08-10T05:00:00",
    )

    assert result.success is True
    assert result.metadata["status"] == "yellow"
    assert result.metadata["leave_by"] == "2026-08-10T05:45:00"
    assert "Drive with traffic: 30m" in result.content


def test_commute_refuses_unstructured_search_fallback(monkeypatch):
    def fake_traffic(self, **params):
        return ToolResult(
            tool_name="traffic_lookup",
            content="search snippet",
            success=True,
            metadata={"provider": "web_search_fallback"},
        )

    monkeypatch.setattr(TrafficTool, "execute", fake_traffic)
    result = CommuteReadinessTool().execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        current_time="2026-08-10T05:00:00",
    )

    assert result.success is False
    assert result.metadata["provider_confidence"] == "insufficient"


def test_commute_rejects_explicit_deadline_that_already_passed(monkeypatch):
    def fail_if_called(self, **params):
        raise AssertionError("traffic should not be queried for a past deadline")

    monkeypatch.setattr(TrafficTool, "execute", fail_if_called)
    result = CommuteReadinessTool().execute(
        origin="Home",
        destination="Base",
        arrival_time="2026-08-09T06:30:00",
        buffer_minutes=15,
        current_time="2026-08-09T15:53:00",
        operating_window="00:00-23:59",
    )

    assert result.success is False
    assert "arrival_time is in the past" in result.content


def test_commute_does_not_run_outside_morning_window(monkeypatch):
    def fail_if_called(self, **params):
        raise AssertionError("traffic should not be queried outside the active window")

    monkeypatch.setattr(TrafficTool, "execute", fail_if_called)
    result = CommuteReadinessTool().execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        buffer_minutes=15,
        current_time="2026-08-09T15:53:00",
    )

    assert result.success is False
    assert result.metadata["reason"] == "outside_operating_window"


def test_commute_test_mode_allows_a_live_check_outside_window(monkeypatch):
    calls = []

    def fake_traffic(self, **params):
        calls.append(params)
        return ToolResult(
            tool_name="traffic_lookup",
            content="live",
            success=True,
            metadata={
                "provider": "google_maps",
                "normal_duration_seconds": 20 * 60,
                "traffic_duration_seconds": 20 * 60,
            },
        )

    monkeypatch.setattr(TrafficTool, "execute", fake_traffic)
    result = CommuteReadinessTool().execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        buffer_minutes=15,
        current_time="2026-08-09T15:53:00",
        test_mode=True,
    )

    assert result.success is True
    assert result.metadata["test_mode"] is True
    assert calls[0]["departure_time"] == "2026-08-09T15:53:00"
    assert len(calls) == 1
    assert result.metadata["traffic_basis"] == "live_now"
    assert "Commute readiness TEST ONLY: GREEN" in result.content
    assert "Traffic observed: 3:53 PM" in result.content


def test_commute_test_mode_sends_one_labeled_message(monkeypatch):
    sent = {}

    class FakeChannel:
        def send(self, target, content, conversation_id=""):
            sent.update(target=target, content=content)
            return True

    def fake_traffic(self, **params):
        return ToolResult(
            tool_name="traffic_lookup",
            content="live",
            success=True,
            metadata={
                "provider": "google_maps",
                "normal_duration_seconds": 20 * 60,
                "traffic_duration_seconds": 20 * 60,
            },
        )

    monkeypatch.setattr(TrafficTool, "execute", fake_traffic)
    tool = CommuteReadinessTool()
    tool._channel = FakeChannel()
    result = tool.execute(
        origin="Home",
        destination="Base",
        arrival_time="06:30",
        buffer_minutes=15,
        current_time="2026-08-09T15:53:00",
        test_mode=True,
        send_test_to="+15551234567",
    )

    assert result.success is True
    assert result.metadata["test_send_status"] == "sent"
    assert sent["target"] == "+15551234567"
    assert sent["content"].startswith("TEST ONLY — LIVE TRAFFIC")
