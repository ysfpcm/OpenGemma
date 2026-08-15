from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjarvis.context import ContextBuilder, ContextRequest, ContextStore
from openjarvis.context.home_assistant import (
    HomeAssistantBridge,
    HomeAssistantContextSource,
    redact_secrets,
)
from openjarvis.core.events import EventBus, EventType


def _source(
    tmp_path: Path,
    *,
    url: str = "http://homeassistant.local:8123",
) -> tuple[ContextStore, HomeAssistantContextSource]:
    store = ContextStore(tmp_path / "context.db")
    return store, HomeAssistantContextSource(
        store,
        url=url,
        token="test-token",
        stale_after_seconds=60,
    )


def test_websocket_url_uses_home_assistant_endpoint(tmp_path: Path) -> None:
    first_store, first_source = _source(tmp_path)
    assert first_source.websocket_url == "ws://homeassistant.local:8123/api/websocket"
    first_store.close()

    second_store, second_source = _source(
        tmp_path,
        url="https://ha.example/api/websocket",
    )
    assert second_source.websocket_url == (
        "wss://ha.example/api/websocket"
    )
    second_store.close()


def test_home_assistant_credential_aliases_are_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    monkeypatch.setenv("HOME_ASSISTANT_URL", "http://nest-ha.local:8123")
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "test-token")

    store = ContextStore(tmp_path / "context.db")
    source = HomeAssistantContextSource(store)

    assert source.is_configured
    assert source.websocket_url == "ws://nest-ha.local:8123/api/websocket"
    store.close()


def test_state_changed_event_populates_context_store(tmp_path: Path) -> None:
    store, source = _source(tmp_path)
    result = source.ingest_websocket_message(
        {
            "type": "event",
            "event": {
                "event_type": "state_changed",
                "time_fired": "2026-08-08T10:00:00Z",
                "data": {
                    "entity_id": "light.living_room",
                    "old_state": {"state": "off"},
                    "new_state": {
                        "entity_id": "light.living_room",
                        "state": "on",
                        "attributes": {
                            "friendly_name": "Living Room Light",
                            "brightness": 180,
                        },
                    },
                },
            },
        }
    )

    assert result is not None and result.inserted
    snapshot = ContextBuilder(store).build(
        ContextRequest(now="2026-08-08T10:00:01Z")
    )
    assert snapshot.entities[0].entity_name == "Living Room Light"
    snapshot_states = [
        (state.state_key, state.value) for state in snapshot.entities[0].states
    ]
    assert snapshot_states == [
        ("brightness", 180),
        ("state", "on"),
    ]
    assert snapshot.recent_events[0].payload == {
        "old_state": "off",
        "new_state": "on",
        "state": {
            "brightness": {"unit": "", "value": 180},
            "state": {"unit": "", "value": "on"},
        },
    }
    store.close()


def test_state_changed_publishes_a_live_context_update(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.db")
    bus = EventBus(record_history=True)
    source = HomeAssistantContextSource(
        store,
        url="http://homeassistant.local:8123",
        token="test-token",
        bus=bus,
    )

    result = source.ingest_state_changed(
        {
            "event_type": "state_changed",
            "time_fired": "2026-08-08T10:00:00Z",
            "data": {
                "entity_id": "binary_sensor.front_person",
                "new_state": {
                    "entity_id": "binary_sensor.front_person",
                    "state": "on",
                    "attributes": {
                        "friendly_name": "Front Person",
                        "device_class": "occupancy",
                    },
                },
            },
        }
    )

    assert result is not None and result.inserted
    updates = [
        event for event in bus.history if event.event_type == EventType.CONTEXT_UPDATED
    ]
    assert len(updates) == 1
    assert updates[0].data == {
        "source": "home_assistant",
        "context_event_id": result.event_id,
        "event_type": "state_changed",
        "entity_name": "Front Person",
        "entity_type": "binary_sensor",
        "changed_state_keys": ["state"],
        "updated_state_keys": ["state"],
        "changed_state_count": 1,
        "updated_state_count": 1,
    }
    store.close()


def test_motion_heartbeat_tracks_recent_activity_and_preserves_raw_state(
    tmp_path: Path,
) -> None:
    store = ContextStore(tmp_path / "context.db")
    bus = EventBus(record_history=True)
    source = HomeAssistantContextSource(
        store,
        url="http://homeassistant.local:8123",
        token="test-token",
        bus=bus,
        motion_heartbeat_seconds=120,
        motion_hold_seconds=300,
    )
    entity = "binary_sensor.kitchen_echo_dot_motion"
    source.ingest_state_changed(
        {
            "event_type": "state_changed",
            "time_fired": "2026-08-08T10:00:00Z",
            "data": {
                "entity_id": entity,
                "old_state": {"state": "off"},
                "new_state": {
                    "entity_id": entity,
                    "state": "on",
                    "attributes": {
                        "friendly_name": "Kitchen Echo Dot Motion",
                        "device_class": "motion",
                    },
                },
            },
        }
    )

    base = time.monotonic()
    first = source.emit_motion_heartbeats(
        now_monotonic=base,
        occurred_at="2026-08-08T10:00:02Z",
    )
    assert len(first) == 1 and first[0].inserted
    assert first[0].updated_state_keys == ("recent_activity",)

    source.ingest_state_changed(
        {
            "event_type": "state_changed",
            "time_fired": "2026-08-08T10:01:00Z",
            "data": {
                "entity_id": entity,
                "old_state": {"state": "on"},
                "new_state": {
                    "entity_id": entity,
                    "state": "off",
                    "attributes": {
                        "friendly_name": "Kitchen Echo Dot Motion",
                        "device_class": "motion",
                    },
                },
            },
        }
    )
    held = source.emit_motion_heartbeats(
        now_monotonic=base + 299,
        occurred_at="2026-08-08T10:04:59Z",
    )
    cleared = source.emit_motion_heartbeats(
        now_monotonic=base + 301,
        occurred_at="2026-08-08T10:05:01Z",
    )

    assert held and held[0].inserted
    assert cleared and cleared[0].inserted
    current = {
        (row.external_entity_id, row.state_key): row.value
        for row in store.get_current_state()
        if row.external_entity_id == entity
    }
    assert current[(entity, "state")] == "off"
    assert current[(entity, "recent_activity")] is False
    assert any(event.event_type == EventType.MOTION_HEARTBEAT for event in bus.history)
    assert any(event.event_type == EventType.MOTION_CLEARED for event in bus.history)
    recent_types = {event.event_type for event in store.get_recent_events(limit=20)}
    assert {"motion_heartbeat", "motion_cleared"}.issubset(recent_types)
    store.close()


def test_state_snapshot_uses_units_and_ignores_unrelated_messages(
    tmp_path: Path,
) -> None:
    store, source = _source(tmp_path)
    assert source.ingest_websocket_message({"type": "result", "success": True}) is None
    result = source.ingest_state_snapshot(
        {
            "entity_id": "sensor.living_room_temperature",
            "state": "73.2",
            "last_updated": "2026-08-08T10:00:00Z",
            "attributes": {
                "friendly_name": "Living Room Temperature",
                "unit_of_measurement": "°F",
                "device_class": "temperature",
            },
        }
    )

    assert result is not None and result.inserted
    state = store.get_current_state()[0]
    assert (state.state_key, state.value, state.unit) == ("state", "73.2", "°F")
    store.close()


def test_home_assistant_event_deduplication_does_not_regress_source_status(
    tmp_path: Path,
) -> None:
    store, source = _source(tmp_path)
    event = {
        "event_type": "state_changed",
        "time_fired": "2026-08-08T10:00:00Z",
        "data": {
            "entity_id": "binary_sensor.front_door",
            "new_state": {
                "entity_id": "binary_sensor.front_door",
                "state": "off",
                "attributes": {"friendly_name": "Front Door"},
            },
        },
    }
    first = source.ingest_state_changed(event)
    duplicate = source.ingest_state_changed(event)

    assert first is not None and first.inserted
    assert duplicate is not None and duplicate.duplicate
    assert store._conn.execute("SELECT COUNT(*) FROM context_events").fetchone()[0] == 1
    store.close()


def test_camera_event_normalization_covers_relevant_capabilities(tmp_path: Path) -> None:
    store, source = _source(tmp_path)
    cases = [
        ("binary_sensor.front_motion", "Front Motion", "motion", "on", "camera_motion"),
        ("binary_sensor.front_person", "Front Person", "person", "on", "person_detected"),
        ("binary_sensor.front_sound", "Front Sound", "sound", "on", "sound_detected"),
        ("event.front_doorbell", "Front Doorbell", "doorbell", "on", "doorbell_chime"),
        ("camera.front_door", "Front Door Camera", None, "idle", "camera_status"),
    ]
    for entity_id, name, device_class, state, expected_type in cases:
        attributes = {"friendly_name": name}
        if device_class:
            attributes["device_class"] = device_class
        normalized = source.normalize_event(
            {
                "event_type": "state_changed",
                "time_fired": "2026-08-08T10:00:00Z",
                "data": {
                    "entity_id": entity_id,
                    "new_state": {
                        "entity_id": entity_id,
                        "state": state,
                        "attributes": attributes,
                    },
                },
            }
        )
        assert normalized is not None
        assert normalized.event_type == expected_type
        assert normalized.entity_name == name
    store.close()


def test_camera_streaming_state_does_not_become_a_doorbell_chime(
    tmp_path: Path,
) -> None:
    store, source = _source(tmp_path)
    normalized = source.normalize_event(
        {
            "event_type": "state_changed",
            "time_fired": "2026-08-08T10:00:00Z",
            "data": {
                "entity_id": "camera.front_door_front_door_doorbell",
                "old_state": {
                    "state": "streaming",
                    "attributes": {"friendly_name": "Front door doorbell", "doorbell": True},
                },
                "new_state": {
                    "state": "streaming",
                    "attributes": {"friendly_name": "Front door doorbell", "doorbell": True},
                },
            },
        }
    )

    assert normalized is not None
    assert normalized.event_type == "camera_status"
    assert normalized.event_type != "doorbell_chime"
    store.close()


def test_motion_event_wins_over_doorbell_name(tmp_path: Path) -> None:
    store, source = _source(tmp_path)
    normalized = source.normalize_event(
        {
            "event_type": "state_changed",
            "time_fired": "2026-08-08T10:00:00Z",
            "data": {
                "entity_id": "event.front_door_front_door_doorbell_motion",
                "old_state": {"state": "unknown"},
                "new_state": {
                    "state": "2026-08-08T10:00:00+00:00",
                    "attributes": {"friendly_name": "Front door doorbell Motion"},
                },
            },
        }
    )

    assert normalized is not None
    assert normalized.event_type == "camera_motion"
    assert normalized.snapshot_url == (
        "/api/camera_proxy/camera.front_door_front_door_doorbell"
    )
    store.close()


@pytest.mark.asyncio
async def test_snapshot_is_downloaded_immediately_and_deduplicated(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.db")
    bus = EventBus(record_history=True)
    fetches: list[tuple[str, dict[str, str]]] = []

    async def fetcher(url: str, headers: dict[str, str]) -> tuple[bytes, str]:
        fetches.append((url, headers))
        return b"fake-jpeg", "image/jpeg"

    source = HomeAssistantContextSource(
        store,
        url="http://127.0.0.1:8123",
        token="do-not-leak",
        bus=bus,
        snapshot_dir=tmp_path / "snapshots",
        snapshot_fetcher=fetcher,
    )
    message = {
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "time_fired": "2026-08-08T10:00:00Z",
            "data": {
                "entity_id": "binary_sensor.front_motion",
                "new_state": {
                    "entity_id": "binary_sensor.front_motion",
                    "state": "on",
                    "attributes": {
                        "friendly_name": "Front Motion",
                        "device_class": "motion",
                        "snapshot_url": "http://127.0.0.1:8123/api/snapshot?token=do-not-leak",
                    },
                },
            },
        },
    }

    first = await source.process_websocket_message(message)
    second = await source.process_websocket_message(message)

    assert len(first) == 3  # state_changed, camera_motion, snapshot_received
    assert all(result.inserted for result in first)
    assert len(second) == 2  # duplicate state + duplicate semantic event
    assert len(fetches) == 1
    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "received"
    assert snapshots[0]["local_ref"].startswith("context://snapshots/jpgs/")
    assert "token" not in json.dumps(snapshots)
    snapshot_events = [
        event for event in bus.history if event.event_type == EventType.SNAPSHOT_RECEIVED
    ]
    assert len(snapshot_events) == 1
    assert "do-not-leak" not in json.dumps(bus.history, default=str)
    store.close()


@pytest.mark.asyncio
async def test_motion_event_without_media_captures_camera_proxy_jpeg(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.db")
    fetches: list[tuple[str, dict[str, str]]] = []

    async def fetcher(url: str, headers: dict[str, str]) -> tuple[bytes, str]:
        fetches.append((url, headers))
        return b"camera-proxy-jpeg", "image/jpeg"

    source = HomeAssistantContextSource(
        store,
        url="http://127.0.0.1:8123",
        token="do-not-leak",
        snapshot_dir=tmp_path / "snapshots",
        snapshot_fetcher=fetcher,
    )
    result = await source.process_websocket_message(
        {
            "type": "event",
            "event": {
                "event_type": "state_changed",
                "time_fired": "2026-08-08T10:00:00Z",
                "data": {
                    "entity_id": "event.front_door_front_door_doorbell_motion",
                    "old_state": {"state": "2026-08-08T09:59:00Z"},
                    "new_state": {
                        "entity_id": "event.front_door_front_door_doorbell_motion",
                        "state": "2026-08-08T10:00:00Z",
                        "attributes": {"friendly_name": "Front door doorbell Motion"},
                    },
                },
            },
        }
    )

    assert len(result) == 3
    assert fetches == [
        (
            "http://127.0.0.1:8123/api/camera_proxy/camera.front_door_front_door_doorbell",
            {"Authorization": "Bearer do-not-leak"},
        )
    ]
    snapshots = store.get_snapshots()
    assert snapshots[0]["status"] == "received"
    assert snapshots[0]["content_type"] == "image/jpeg"
    assert snapshots[0]["local_ref"].startswith("context://snapshots/jpgs/")
    store.close()


@pytest.mark.asyncio
async def test_relative_nest_snapshot_url_is_resolved_against_home_assistant(
    tmp_path: Path,
) -> None:
    store = ContextStore(tmp_path / "context.db")
    fetches: list[tuple[str, dict[str, str]]] = []

    async def fetcher(url: str, headers: dict[str, str]) -> tuple[bytes, str]:
        fetches.append((url, headers))
        return b"fake-gif", "image/gif"

    source = HomeAssistantContextSource(
        store,
        url="http://127.0.0.1:8123",
        token="do-not-leak",
        snapshot_dir=tmp_path / "snapshots",
        snapshot_fetcher=fetcher,
    )
    relative_url = "/api/nest/event_media/device/event/thumbnail"
    result = await source.process_websocket_message(
        {
            "type": "event",
            "event": {
                "event_type": "nest_event",
                "time_fired": "2026-08-08T10:00:00Z",
                "data": {
                    "device_id": "device",
                    "type": "camera_person",
                    "nest_event_id": "event",
                    "attachment": {"image": relative_url},
                },
            },
        }
    )

    assert len(result) == 2
    assert fetches == [
        (
            "http://127.0.0.1:8123/api/nest/event_media/device/event/thumbnail",
            {"Authorization": "Bearer do-not-leak"},
        )
    ]
    store.close()


@pytest.mark.asyncio
async def test_nest_image_and_video_attachments_use_separate_media_folders(
    tmp_path: Path,
) -> None:
    store = ContextStore(tmp_path / "context.db")
    fetches: list[str] = []

    async def fetcher(url: str, _: dict[str, str]) -> tuple[bytes, str]:
        fetches.append(url)
        if url.endswith("/video"):
            return b"fake-mp4", "video/mp4"
        return b"fake-gif", "image/gif"

    source = HomeAssistantContextSource(
        store,
        url="http://127.0.0.1:8123",
        token="do-not-leak",
        snapshot_dir=tmp_path / "snapshots",
        snapshot_fetcher=fetcher,
    )
    result = await source.process_websocket_message(
        {
            "type": "event",
            "event": {
                "event_type": "nest_event",
                "time_fired": "2026-08-08T10:00:00Z",
                "data": {
                    "device_id": "device",
                    "type": "camera_person",
                    "nest_event_id": "event",
                    "attachment": {
                        "image": "/api/nest/event_media/device/event/thumbnail",
                        "video": "/api/nest/event_media/device/event/video",
                    },
                },
            },
        }
    )

    assert len(result) == 3
    snapshots = sorted(store.get_snapshots(), key=lambda item: item["local_ref"])
    assert [snapshot["content_type"] for snapshot in snapshots] == [
        "image/gif",
        "video/mp4",
    ]
    assert snapshots[0]["local_ref"].startswith("context://snapshots/gifs/")
    assert snapshots[1]["local_ref"].startswith("context://snapshots/videos/")
    assert (tmp_path / "snapshots" / "gifs").is_dir()
    assert (tmp_path / "snapshots" / "videos").is_dir()
    assert len(fetches) == 2
    store.close()


@pytest.mark.asyncio
async def test_snapshot_failure_is_safe_and_recorded(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.db")

    async def failing_fetcher(_: str, __: dict[str, str]) -> bytes:
        raise RuntimeError("expired source URL must not be logged")

    source = HomeAssistantContextSource(
        store,
        url="http://127.0.0.1:8123",
        token="do-not-leak",
        snapshot_dir=tmp_path / "snapshots",
        snapshot_fetcher=failing_fetcher,
    )
    result = await source.process_websocket_message(
        {
            "type": "event",
            "event": {
                "event_type": "camera_event",
                "time_fired": "2026-08-08T10:00:00Z",
                "data": {
                    "entity_id": "camera.front_door",
                    "event_type": "person_detected",
                    "snapshot_url": "http://127.0.0.1:8123/api/snapshot?token=do-not-leak",
                },
            },
        }
    )
    assert len(result) == 2
    snapshots = store.get_snapshots()
    assert snapshots[0]["status"] == "failed"
    assert snapshots[0]["error_code"] == "snapshot_unavailable"
    assert "do-not-leak" not in json.dumps(snapshots)
    store.close()


@pytest.mark.asyncio
async def test_bridge_reconnects_with_backoff_after_disconnect(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.db")
    bus = EventBus(record_history=True)
    calls = 0

    class FakeSource:
        async def listen(self) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("connection dropped")
            bridge._stop.set()

    async def no_wait(_: float) -> None:
        return None

    bridge = HomeAssistantBridge(
        store,
        token="configured",
        bus=bus,
        source_factory=FakeSource,
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.02,
        sleeper=no_wait,
    )
    await bridge.run_forever()

    assert calls == 2
    statuses = [
        event.data["status"]
        for event in bus.history
        if event.event_type == EventType.HOME_ASSISTANT_STATUS
    ]
    assert statuses.count("offline") >= 2
    store.close()


def test_secret_redaction_removes_tokens_and_urls() -> None:
    safe = redact_secrets(
        {
            "access_token": "secret-token",
            "snapshot_url": "https://camera.example/snapshot?token=secret-token",
            "label": "Front Door",
        }
    )
    assert safe == {
        "access_token": "[REDACTED]",
        "snapshot_url": "[URL_REDACTED]",
        "label": "Front Door",
    }


@pytest.mark.asyncio
async def test_listen_uses_mocked_home_assistant_websocket_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ContextStore(tmp_path / "context.db")
    bus = EventBus(record_history=True)

    class FakeSocket:
        def __init__(self) -> None:
            self.responses = [
                {"type": "auth_required"},
                {"type": "auth_ok", "ha_version": "mock"},
                {"id": 1, "type": "result", "success": True, "result": [
                    {
                        "entity_id": "camera.front_door",
                        "state": "idle",
                        "last_updated": "2026-08-08T10:00:00Z",
                        "attributes": {"friendly_name": "Front Door Camera"},
                    }
                ]},
                {"id": 2, "type": "result", "success": True, "result": True},
            ]
            self.events = [
                {
                    "type": "event",
                    "event": {
                        "event_type": "state_changed",
                        "time_fired": "2026-08-08T10:00:01Z",
                        "data": {
                            "entity_id": "binary_sensor.front_motion",
                            "new_state": {
                                "entity_id": "binary_sensor.front_motion",
                                "state": "on",
                                "attributes": {
                                    "friendly_name": "Front Motion",
                                    "device_class": "motion",
                                },
                            },
                        },
                    },
                }
            ]
            self.sent: list[dict[str, object]] = []

        async def recv(self) -> str:
            return json.dumps(self.responses.pop(0))

        async def send(self, message: str) -> None:
            self.sent.append(json.loads(message))

        def __aiter__(self) -> "FakeSocket":
            return self

        async def __anext__(self) -> str:
            if not self.events:
                raise StopAsyncIteration
            return json.dumps(self.events.pop(0))

    socket = FakeSocket()

    class FakeConnection:
        async def __aenter__(self) -> FakeSocket:
            return socket

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setitem(
        sys.modules,
        "websockets",
        SimpleNamespace(connect=lambda *args, **kwargs: FakeConnection()),
    )
    source = HomeAssistantContextSource(
        store,
        url="http://127.0.0.1:8123",
        token="mock-token",
        bus=bus,
    )

    await source.listen()

    assert [message["type"] for message in socket.sent] == [
        "auth",
        "get_states",
        "subscribe_events",
    ]
    assert any(event.event_type == EventType.CAMERA_STATUS for event in bus.history)
    assert any(event.event_type == EventType.CAMERA_MOTION for event in bus.history)
    assert store.get_source_health()[0].status == "offline"
    store.close()
