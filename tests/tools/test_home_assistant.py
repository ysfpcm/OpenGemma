"""Regression tests for verified Home Assistant controls."""

from __future__ import annotations

from openjarvis.tools.home_assistant import HomeAssistantTool

_STATES = [
    {
        "entity_id": "sensor.kitchen_echo_dot_temperature",
        "state": "68.72",
        "attributes": {
            "friendly_name": "Kitchen Echo Dot Temperature",
            "device_class": "temperature",
            "unit_of_measurement": "°F",
        },
    },
    {
        "entity_id": "light.livingroomlamp",
        "state": "on",
        "attributes": {"friendly_name": "Living Room Lamp", "brightness": 128},
    },
]


def test_default_temperature_prefers_kitchen_echo_dot(monkeypatch) -> None:
    tool = HomeAssistantTool()
    monkeypatch.setattr(tool, "_get_states", lambda: _STATES)

    result = tool.execute(action="get_temperature")

    assert result.success is True
    assert result.content == "Kitchen Echo Dot Temperature is 68.72 °F."


def test_turn_off_is_rejected_without_guardian_authorization(monkeypatch) -> None:
    tool = HomeAssistantTool()
    recorded: list[tuple[str, str, str]] = []
    monkeypatch.setattr(tool, "_get_states", lambda: _STATES)
    monkeypatch.setattr(
        tool,
        "_call_service",
        lambda domain, service, entity_id: recorded.append(
            (domain, service, entity_id)
        ),
    )
    result = tool.execute(action="turn_off", entity="living room lamp")

    assert result.success is False
    assert "guardian authorization" in result.content.lower()
    assert recorded == []


def test_unguarded_home_write_does_not_call_service(monkeypatch) -> None:
    tool = HomeAssistantTool()
    monkeypatch.setattr(tool, "_get_states", lambda: _STATES)
    monkeypatch.setattr(tool, "_call_service", lambda *_: None)
    result = tool.execute(action="turn_off", entity="living room lamp")

    assert result.success is False
    assert "guardian authorization" in result.content.lower()
