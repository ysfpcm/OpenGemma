"""Deterministic commute-readiness calculation.

This tool owns the time arithmetic for departure alerts.  The language model
can explain the result and notify the user, but it should not be responsible
for parsing travel durations or deciding whether a live-traffic result is
safe enough to act on.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.channel_tools import ChannelSendTool
from openjarvis.tools.traffic_tool import TrafficTool


def _parse_time(value: str, now: datetime) -> datetime:
    """Parse an ISO datetime or local HH:MM arrival time.

    A time-only deadline means the next occurrence of that clock time.  An
    explicit ISO deadline is authoritative; silently accepting an explicit
    deadline that is already past is unsafe for an alerting agent.
    """
    value = value.strip()
    if not value:
        raise ValueError("arrival_time is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        if parsed <= now:
            raise ValueError(
                "arrival_time is in the past; use the next date or omit the date "
                "to use the next occurrence"
            )
        return parsed

    for fmt in ("%H:%M", "%I:%M %p", "%I %p"):
        try:
            parsed_time = datetime.strptime(value.upper(), fmt).time()
            result = now.replace(
                hour=parsed_time.hour,
                minute=parsed_time.minute,
                second=0,
                microsecond=0,
            )
            if result <= now:
                result += timedelta(days=1)
            return result
        except ValueError:
            continue
    raise ValueError("arrival_time must be ISO-8601 or HH:MM")


def _format_duration(seconds: int) -> str:
    minutes = max(0, round(seconds / 60))
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins:02d}m" if mins else f"{hours}h"
    return f"{mins}m"


def _format_clock(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _parse_window(value: str) -> tuple[time, time]:
    """Parse a local daily window such as ``05:00-06:00``."""
    try:
        start_value, end_value = value.split("-", 1)
        start = datetime.strptime(start_value.strip(), "%H:%M").time()
        end = datetime.strptime(end_value.strip(), "%H:%M").time()
    except (AttributeError, TypeError, ValueError):
        raise ValueError("operating_window must use HH:MM-HH:MM") from None
    if start == end:
        raise ValueError("operating_window cannot be empty")
    return start, end


def _in_window(now: datetime, start: time, end: time) -> bool:
    """Return whether *now* is inside a same-day or overnight window."""
    current = now.time()
    if start < end:
        return start <= current <= end
    return current >= start or current <= end


@ToolRegistry.register("commute_readiness")
class CommuteReadinessTool(BaseTool):
    """Calculate a live-traffic departure recommendation."""

    tool_id = "commute_readiness"
    # Populated by AgentExecutor with the live channel bridge so an explicit
    # test_mode request can send its single labeled result atomically.
    _channel: Any = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="commute_readiness",
            description=(
                "Calculate whether the user can reach a destination on time "
                "using live traffic. Test mode queries current traffic immediately. "
                "Returns normal duration, traffic duration, "
                "delay, leave-by time, and green/yellow/red readiness."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Starting address."},
                    "destination": {"type": "string", "description": "Destination address."},
                    "arrival_time": {
                        "type": "string",
                        "description": "Arrival deadline as HH:MM, 6:30 AM, or ISO-8601.",
                    },
                    "buffer_minutes": {
                        "type": "integer",
                        "description": "Minutes to preserve after the drive, usually 10-20.",
                    },
                    "current_time": {
                        "type": "string",
                        "description": "Optional ISO-8601 time for testing or replay.",
                    },
                    "operating_window": {
                        "type": "string",
                        "description": (
                            "Optional local daily operating window as HH:MM-HH:MM. "
                            "Defaults to 05:00-06:00 for the morning commute agent."
                        ),
                    },
                    "test_mode": {
                        "type": "boolean",
                        "description": (
                            "Explicit manual test only. Bypasses the operating window, "
                            "marks the result as TEST ONLY, and must never be treated "
                            "as a scheduled morning forecast."
                        ),
                    },
                    "send_test_to": {
                        "type": "string",
                        "description": (
                            "SendBlue destination for an explicit test_mode message. "
                            "The tool sends exactly one TEST ONLY message itself."
                        ),
                    },
                },
                "required": ["origin", "destination", "arrival_time"],
            },
            category="search",
        )

    def execute(self, **params: Any) -> ToolResult:
        origin = str(params.get("origin", "")).strip()
        destination = str(params.get("destination", "")).strip()
        arrival_value = str(params.get("arrival_time", "")).strip()
        if not origin or not destination or not arrival_value:
            return ToolResult(
                tool_name=self.tool_id,
                content="origin, destination, and arrival_time are required.",
                success=False,
            )

        try:
            now_value = str(params.get("current_time", "")).strip()
            now = datetime.fromisoformat(now_value) if now_value else datetime.now()
            if now.tzinfo is not None:
                now = now.replace(tzinfo=None)
            operating_window = str(
                params.get("operating_window", "05:00-06:00")
            ).strip()
            test_mode = bool(params.get("test_mode", False))
            window_start, window_end = _parse_window(operating_window)
            if not test_mode and not _in_window(now, window_start, window_end):
                return ToolResult(
                    tool_name=self.tool_id,
                    content=(
                        "No commute check performed: the agent is outside its "
                        f"operating window ({operating_window} local time). "
                        "Do not send an alert."
                    ),
                    success=False,
                    metadata={
                        "reason": "outside_operating_window",
                        "operating_window": operating_window,
                        "current_time": now.isoformat(),
                    },
                )
            arrival = _parse_time(arrival_value, now)
            buffer_minutes = int(params.get("buffer_minutes", 15))
            if buffer_minutes < 0 or buffer_minutes > 240:
                raise ValueError("buffer_minutes must be between 0 and 240")
        except (TypeError, ValueError) as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Invalid commute parameters: {exc}",
                success=False,
            )

        traffic = TrafficTool().execute(
            origin=origin,
            destination=destination,
            departure_time=now.isoformat(),
        )
        if not traffic.success:
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    "Live traffic is unavailable. Do not use this result to "
                    f"make a time-critical departure decision. {traffic.content}"
                ),
                success=False,
                metadata={"provider_confidence": "unavailable"},
            )

        metadata = traffic.metadata or {}
        normal_seconds = metadata.get("normal_duration_seconds")
        traffic_seconds = metadata.get("traffic_duration_seconds")
        provider = metadata.get("provider", "unknown")

        # Search snippets can look plausible but do not provide the structured
        # durations required for a safe leave-by calculation.
        if not isinstance(normal_seconds, (int, float)) or not isinstance(
            traffic_seconds, (int, float)
        ):
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    "Live traffic did not return structured travel durations. "
                    "Do not use this result for a time-critical departure."
                ),
                success=False,
                metadata={
                    "provider": provider,
                    "provider_confidence": "insufficient",
                },
            )

        normal_seconds = int(normal_seconds)

        traffic_query_time = now
        traffic_basis = "live_now"
        traffic_seconds = int(traffic_seconds)

        delay_seconds = max(0, traffic_seconds - normal_seconds)
        leave_by = arrival - timedelta(seconds=traffic_seconds + buffer_minutes * 60)

        if now > leave_by:
            status = "red"
        elif now + timedelta(minutes=5) >= leave_by or delay_seconds >= 10 * 60:
            status = "yellow"
        else:
            status = "green"

        traffic_time_label = (
            f"Traffic observed: {_format_clock(traffic_query_time)} (live lookup)"
        )
        content = (
            f"Commute readiness{' TEST ONLY' if test_mode else ''}: {status.upper()}\n"
            f"Origin: {origin}\n"
            f"Destination: {destination}\n"
            f"Arrival target: {_format_clock(arrival)}\n"
            f"Current time: {_format_clock(now)}\n"
            f"{traffic_time_label}\n"
            f"Normal drive: {_format_duration(normal_seconds)}\n"
            f"Drive with traffic: {_format_duration(traffic_seconds)}\n"
            f"Traffic delay: {_format_duration(delay_seconds)}\n"
            f"Buffer: {buffer_minutes}m\n"
            f"Leave by: {_format_clock(leave_by)}\n"
            f"Provider: {provider}"
        )

        test_send_to = str(params.get("send_test_to", "")).strip()
        if test_mode and test_send_to:
            test_message = (
                "TEST ONLY — LIVE TRAFFIC\n"
                f"Observation time: {traffic_query_time.isoformat()} "
                f"({traffic_query_time.strftime('%A, %B %d, %Y')} at "
                f"{_format_clock(traffic_query_time)} local)\n\n"
                f"Status: {status.upper()}\n"
                f"- Normal drive time: {_format_duration(normal_seconds)}\n"
                f"- Drive with traffic: {_format_duration(traffic_seconds)}\n"
                f"- Traffic delay: {_format_duration(delay_seconds)}\n"
                f"- Buffer added: {buffer_minutes} minutes\n"
                f"- Leave-by time: {_format_clock(leave_by)} "
                f"(to arrive by {_format_clock(arrival)})\n"
                f"Provider: {provider}\n\n"
                "This is a live test lookup at current local conditions, "
                "not a forecast for Monday or any future date."
            )
            send_result = ChannelSendTool(
                channel=getattr(self, "_channel", None)
            ).execute(channel=test_send_to, content=test_message)
            if not send_result.success:
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"{content}\nTest SendBlue delivery failed: {send_result.content}",
                    success=False,
                    metadata={
                        "status": status,
                        "provider": provider,
                        "provider_confidence": "live_structured",
                        "test_mode": True,
                        "test_send_to": test_send_to,
                        "test_send_status": "failed",
                        "normal_duration_seconds": normal_seconds,
                        "traffic_duration_seconds": traffic_seconds,
                        "delay_seconds": delay_seconds,
                        "arrival_time": arrival.isoformat(),
                        "leave_by": leave_by.isoformat(),
                        "traffic_observed_at": now.isoformat(),
                        "operating_window": operating_window,
                    },
                )
            content += f"\nTest SendBlue delivery: sent to {test_send_to}"

        return ToolResult(
            tool_name=self.tool_id,
            content=content,
            success=True,
            metadata={
                "status": status,
                "provider": provider,
                "provider_confidence": "live_structured",
                "normal_duration_seconds": normal_seconds,
                "traffic_duration_seconds": traffic_seconds,
                "delay_seconds": delay_seconds,
                "arrival_time": arrival.isoformat(),
                "leave_by": leave_by.isoformat(),
                "traffic_observed_at": now.isoformat(),
                "traffic_query_time": traffic_query_time.isoformat(),
                "traffic_basis": traffic_basis,
                "operating_window": operating_window,
                "test_mode": test_mode,
                "test_send_to": test_send_to,
                "test_send_status": "sent" if test_mode and test_send_to else "not_requested",
            },
        )


__all__ = ["CommuteReadinessTool"]
