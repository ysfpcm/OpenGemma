"""Tests for the confirmation-gated Home Assistant command bridge."""

from __future__ import annotations

import httpx

from openjarvis.tools.home_assistant import HomeAssistantCommandTool


def test_missing_token_does_not_call_home_assistant(monkeypatch) -> None:
    monkeypatch.delenv("HOME_ASSISTANT_TOKEN", raising=False)

    result = HomeAssistantCommandTool().execute(
        operation="alexa_text", text_command="play rain sounds"
    )

    assert not result.success
    assert "HOME_ASSISTANT_TOKEN" in result.content


def test_sends_any_confirmed_phrase_to_kitchen_echo_dot(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "test-token")
    monkeypatch.setenv("HOME_ASSISTANT_URL", "http://ha.test:8123/")
    monkeypatch.setattr("openjarvis.tools.home_assistant.httpx.post", fake_post)

    result = HomeAssistantCommandTool().execute(
        operation="alexa_text", target="kitchen", text_command="set volume to 3"
    )

    assert result.success
    assert result.content == "Sent to the Kitchen Echo Dot: set volume to 3"
    assert calls == [
        {
            "url": "http://ha.test:8123/api/services/alexa_devices/send_text_command",
            "headers": {"Authorization": "Bearer test-token"},
            "json": {
                "device_id": "ac47606d23d7b56afc8687a71501cc89",
                "text_command": "set volume to 3",
            },
            "timeout": 15.0,
        }
    ]


def test_bedroom_target_uses_its_configured_speaker(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "test-token")
    monkeypatch.setattr("openjarvis.tools.home_assistant.httpx.post", fake_post)

    result = HomeAssistantCommandTool().execute(
        operation="alexa_text", target="bedroom", text_command="play rain sounds"
    )

    assert result.success
    assert calls[0]["json"]["device_id"] == "3deed799cc53eb1274c8a0b08bdab124"


def test_configured_speaker_can_be_added_without_code_changes(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "test-token")
    monkeypatch.setenv(
        "HOME_ASSISTANT_ALEXA_DEVICES",
        '{"floor_1":{"id":"floor-device","name":"Floor 1"}}',
    )
    monkeypatch.setattr("openjarvis.tools.home_assistant.httpx.post", fake_post)

    result = HomeAssistantCommandTool().execute(
        operation="alexa_text", target="floor_1", text_command="turn on the lights"
    )

    assert result.success
    assert calls[0]["json"]["device_id"] == "floor-device"


def test_http_error_is_reported_without_exposing_token(monkeypatch) -> None:
    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "do-not-expose")
    monkeypatch.setattr("openjarvis.tools.home_assistant.httpx.post", fake_post)

    result = HomeAssistantCommandTool().execute(operation="alexa_text", text_command="stop")

    assert not result.success
    assert "HTTP 401" in result.content
    assert "do-not-expose" not in result.content


def test_direct_light_control_uses_light_service(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "test-token")
    monkeypatch.setattr("openjarvis.tools.home_assistant.httpx.post", fake_post)

    result = HomeAssistantCommandTool().execute(
        operation="turn_on",
        entity_id="light.kitchen",
        brightness_pct=45,
        color_name="warm white",
    )

    assert result.success
    assert calls[0]["url"].endswith("/api/services/light/turn_on")
    assert calls[0]["json"] == {
        "entity_id": "light.kitchen",
        "brightness_pct": 45,
        "color_name": "warm white",
    }


def test_direct_control_rejects_unapproved_entity_domains(monkeypatch) -> None:
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "test-token")

    result = HomeAssistantCommandTool().execute(
        operation="turn_off", entity_id="lock.front_door"
    )

    assert not result.success
    assert "light, switch, fan, or scene" in result.content


def test_media_player_volume_is_limited_to_zero_through_one(monkeypatch) -> None:
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "test-token")

    result = HomeAssistantCommandTool().execute(
        operation="set_volume",
        entity_id="media_player.kitchen_echo_dot",
        volume_level=1.1,
    )

    assert not result.success
    assert "0 to 1" in result.content
