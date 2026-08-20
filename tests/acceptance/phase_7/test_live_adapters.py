from __future__ import annotations

from typing import Any

import httpx

from openjarvis.departure import LiveHomeAssistantAdapter, LiveNotificationAdapter


def test_live_home_assistant_adapter_reads_and_calls_real_rest_boundary(
    monkeypatch,
):
    requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: float,
    ) -> httpx.Response:
        requests.append((method, url, json))
        if method == "GET":
            return httpx.Response(
                200,
                json={
                    "entity_id": "climate.home",
                    "state": "heat",
                    "attributes": {
                        "preset_mode": "home",
                        "temperature": 69,
                    },
                },
                request=httpx.Request(method, url),
            )
        return httpx.Response(
            200,
            json=[{"entity_id": "climate.home", "state": "heat"}],
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", request)
    adapter = LiveHomeAssistantAdapter(url="http://ha.local:8123", token="test-token")

    state = adapter.read_state("climate.home")
    outcome = adapter.apply(
        "home_assistant.thermostat.preset",
        "climate.home",
        {"target": "climate.home", "preset": "away"},
    )

    assert state["preset_mode"] == "home"
    assert outcome.success is True
    assert requests == [
        ("GET", "http://ha.local:8123/api/states/climate.home", None),
        (
            "POST",
            "http://ha.local:8123/api/services/climate/set_preset_mode",
            {"entity_id": "climate.home", "preset_mode": "away"},
        ),
    ]


def test_live_notification_adapter_uses_configured_channel_bridge():
    sent: list[tuple[str, str]] = []

    class Bridge:
        def list_channels(self) -> list[str]:
            return ["marc-phone"]

        def send(self, target: str, message: str) -> bool:
            sent.append((target, message))
            return True

    adapter = LiveNotificationAdapter(Bridge())

    outcome = adapter.deliver(
        "marc-phone",
        {"target": "marc-phone", "message": "Leave in 15 minutes."},
    )

    assert outcome.success is True
    assert sent == [("marc-phone", "Leave in 15 minutes.")]
    assert adapter.verify(
        "marc-phone", {"target": "marc-phone", "message": "Leave in 15 minutes."}
    )[0]
