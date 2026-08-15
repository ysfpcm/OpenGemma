"""Local authoritative clock tool."""

from __future__ import annotations

import json
from typing import Any

from openjarvis.context.runtime import clock_snapshot
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("clock")
class ClockTool(BaseTool):
    """Return the current date and time from the host running OpenJarvis."""

    tool_id = "clock"

    def __init__(self, config: Any = None) -> None:
        self._config = config

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="clock",
            description=(
                "Return the authoritative current date, time, weekday, timezone, "
                "and ISO week number. Use this for questions about today, now, "
                "the current date/time, or the day of the week."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": (
                            "Optional IANA timezone such as America/Denver. "
                            "Omit to use the host's configured local timezone."
                        ),
                    }
                },
                "required": [],
            },
            category="runtime",
        )

    def execute(self, **params: Any) -> ToolResult:
        timezone_name = params.get("timezone")
        if timezone_name is not None and not isinstance(timezone_name, str):
            return ToolResult(
                tool_name=self.tool_id,
                content="timezone must be an IANA timezone string.",
                success=False,
            )

        snapshot = clock_snapshot(
            config=self._config,
            timezone_name=timezone_name or None,
        )
        return ToolResult(
            tool_name=self.tool_id,
            content=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            success=True,
            metadata=snapshot,
        )


__all__ = ["ClockTool"]
