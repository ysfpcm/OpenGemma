import json

from openjarvis.tools.clock import ClockTool


def test_clock_tool_returns_structured_current_clock_data():
    result = ClockTool().execute()

    assert result.success is True
    data = json.loads(result.content)
    assert set(("date", "time", "timezone", "iso", "weekday", "week_number")) <= set(data)
    assert result.metadata["date"] == data["date"]


def test_clock_tool_rejects_non_string_timezone():
    result = ClockTool().execute(timezone=42)

    assert result.success is False
    assert "timezone" in result.content
