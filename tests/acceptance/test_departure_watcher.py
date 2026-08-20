from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from openjarvis.cognition import ActionLedger
from openjarvis.context import ContextStore
from openjarvis.context.home_assistant import HomeAssistantContextSource
from openjarvis.core.events import EventBus
from openjarvis.departure import (
    DepartureWatcherService,
    FakeHomeAssistantAdapter,
    FakeNotificationAdapter,
    register_departure_adapters,
)
from openjarvis.guardian import ActionRegistry, GuardianKernel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state(source: HomeAssistantContextSource, entity_id: str, state: str, name: str) -> None:
    _state_at(source, entity_id, state, name, _now())


def _state_at(
    source: HomeAssistantContextSource,
    entity_id: str,
    state: str,
    name: str,
    timestamp: datetime,
) -> None:
    source.ingest_state_changed(
        {
            "event_type": "state_changed",
            "time_fired": timestamp.isoformat(),
            "data": {
                "entity_id": entity_id,
                "new_state": {
                    "entity_id": entity_id,
                    "state": state,
                    "attributes": {
                        "friendly_name": name,
                        "device_class": "occupancy"
                        if entity_id.startswith("binary_sensor")
                        else None,
                    },
                },
            },
        }
    )


def _camera_event(source: HomeAssistantContextSource) -> int:
    timestamp = _now().isoformat()
    normalized = source.normalize_event(
        {
            "event_type": "state_changed",
            "time_fired": timestamp,
            "data": {
                "entity_id": "event.front_door_motion",
                "old_state": {"state": "off"},
                "new_state": {
                    "entity_id": "event.front_door_motion",
                    "state": timestamp,
                    "attributes": {"friendly_name": "Front Door Motion"},
                },
            },
        }
    )
    assert normalized is not None
    result, _ = source._apply_normalized_event(normalized)
    return result.event_id


def test_simulation_is_durable_one_shot_and_never_calls_guardian(tmp_path) -> None:
    store = ContextStore(tmp_path / "context.db")
    source = HomeAssistantContextSource(
        store,
        url="http://ha:8123",
        token="test-token",
        stale_after_seconds=300,
    )
    _state(source, "camera.front_door", "on", "Front Door Camera")
    _state(source, "binary_sensor.occupancy", "off", "Home Occupancy")
    _state(source, "light.living_room_lamp", "on", "Living Room Lamp")
    service = DepartureWatcherService(
        store,
        home_assistant_bridge=SimpleNamespace(is_configured=True),
        guardian=SimpleNamespace(),
        monitor_available=True,
    )
    created = service.create_watcher(
        conversation_id="conversation-1", watcher_id="watcher-1"
    )
    event_id = _camera_event(source)
    first = service.handle_context_event(event_id)
    second = service.handle_context_event(event_id)

    assert first[0]["decision"] == "SIMULATION_PREPARED"
    assert second == []
    report = service.inspect(created["watcher_id"])
    assert report["current_status"] == "COMPLETED"
    assert report["fire_count"] == 1
    assert report["departure_observed"] is False
    assert report["departure_inferred"] is True
    assert report["action_attempted"]["executed"] is False
    assert report["final_state"]["real_world_state"] == "not_claimed"
    assert report["conversation_id"] == "conversation-1"
    assert report["events"][0]["context_event_id"] == event_id
    store.close()

    reopened = ContextStore(tmp_path / "context.db")
    assert reopened.get_departure_watcher("watcher-1")["status"] == "COMPLETED"
    assert reopened.get_departure_watcher("watcher-1")["device_states"]
    reopened.close()


def test_unavailable_home_assistant_is_not_armed(tmp_path) -> None:
    store = ContextStore(tmp_path / "context.db")
    service = DepartureWatcherService(store, monitor_available=False)
    report = service.create_watcher(
        conversation_id="conversation-2", watcher_id="watcher-2"
    )
    assert report["current_status"] == "NEEDS_ATTENTION"
    assert report["action_attempted"]["status"] == "not_attempted"
    assert "cannot monitor future events" in (report["last_error"] or "")
    store.close()


def test_restart_reloads_active_watcher_from_context_database(tmp_path) -> None:
    path = tmp_path / "context.db"
    store = ContextStore(path)
    first = DepartureWatcherService(
        store,
        home_assistant_bridge=SimpleNamespace(is_configured=True),
        monitor_available=True,
    )
    first.create_watcher(conversation_id="conversation-3", watcher_id="watcher-3")
    store.close()

    reopened = ContextStore(path)
    restarted = DepartureWatcherService(
        reopened,
        home_assistant_bridge=SimpleNamespace(is_configured=True),
        monitor_available=True,
    )
    restarted.start()
    assert restarted.inspect("watcher-3")["current_status"] == "ACTIVE"
    assert "watcher-3" in restarted._active_ids
    restarted.stop()
    reopened.close()


def test_fresh_camera_event_survives_unrelated_stale_context_and_prior_motion(tmp_path) -> None:
    store = ContextStore(tmp_path / "context.db")
    source = HomeAssistantContextSource(
        store,
        url="http://ha:8123",
        token="test-token",
        stale_after_seconds=300,
    )
    _state(source, "camera.front_door", "streaming", "Front Door Camera")
    _state(source, "light.living_room_lamp", "on", "Living Room Lamp")
    stale_at = _now().replace(microsecond=0)
    stale_at = stale_at.replace(year=stale_at.year - 1)
    _state_at(source, "sensor.backup_automatic_backup", "stale", "Backup Automatic backup", stale_at)
    _state_at(source, "person.marc", "unknown", "Marc", stale_at)

    service = DepartureWatcherService(
        store,
        home_assistant_bridge=SimpleNamespace(is_configured=True),
        monitor_available=True,
    )
    created = service.create_watcher(
        conversation_id="conversation-stale-context",
        watcher_id="watcher-stale-context",
    )

    before_camera = _now()
    _state_at(
        source,
        "binary_sensor.kitchen_echo_dot_motion",
        "on",
        "Kitchen Echo Dot Motion",
        before_camera,
    )
    heartbeat = source.emit_motion_heartbeats(occurred_at=before_camera)[0]
    event_id = _camera_event(source)
    results = service.handle_context_event(event_id)

    assert heartbeat.event_id < event_id
    assert results[0]["decision"] == "SIMULATION_PREPARED"
    report = service.inspect(created["watcher_id"])
    evidence = report["observed_evidence"][0]["facts"]
    assert report["current_status"] == "COMPLETED"
    assert report["fire_count"] == 1
    assert evidence["source"]["usable"] is True
    assert evidence["occupancy"]["classification"] == "uncertain"
    assert evidence["recent_conflicts"] == []
    assert report["action_attempted"]["executed"] is False
    store.close()


def test_live_watcher_bus_callback_executes_only_lamp_and_independently_verifies(
    tmp_path,
) -> None:
    """Exercise the production order: persisted event -> bus -> Guardian -> readback."""
    store = ContextStore(tmp_path / "context.db")
    bus = EventBus(record_history=True)

    async def snapshot_fetcher(url, headers):
        assert url == "http://ha:8123/api/camera_proxy/camera.front_door"
        assert headers["Authorization"] == "Bearer test-token"
        return b"jpeg-bytes", "image/jpeg"

    source = HomeAssistantContextSource(
        store,
        url="http://ha:8123",
        token="test-token",
        bus=bus,
        snapshot_dir=tmp_path / "snapshots",
        snapshot_fetcher=snapshot_fetcher,
        stale_after_seconds=300,
    )
    _state(source, "camera.front_door", "streaming", "Front Door Camera")
    _state(source, "binary_sensor.occupancy", "off", "Home Occupancy")
    _state(source, "light.living_room_lamp", "on", "Living Room Lamp")

    home = FakeHomeAssistantAdapter(
        {
            "light.living_room_lamp": {
                "entity_id": "light.living_room_lamp",
                "state": "on",
                "attributes": {},
            }
        }
    )
    guardian = GuardianKernel(ActionLedger(tmp_path / "guardian.db"), ActionRegistry())
    register_departure_adapters(guardian.registry, home, FakeNotificationAdapter())
    service = DepartureWatcherService(
        store,
        guardian=guardian,
        home_assistant_bridge=SimpleNamespace(is_configured=True),
        state_reader=home,
        bus=bus,
        monitor_available=True,
    )
    service.start()
    created = service.create_watcher(
        conversation_id="live-bus-test",
        watcher_id="watcher-live-bus",
        mode="live",
        live_approved=True,
    )
    assert created["current_status"] == "ACTIVE"
    assert created["action"]["target_entity_id"] == "light.living_room_lamp"

    results = asyncio.run(
        source.process_websocket_message(
            {
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "time_fired": _now().isoformat(),
                    "data": {
                        "entity_id": "event.front_door_motion",
                        "old_state": {"state": "off"},
                        "new_state": {
                            "entity_id": "event.front_door_motion",
                            "state": _now().isoformat(),
                            "attributes": {
                                "friendly_name": "Front Door Motion",
                            },
                        },
                    },
                },
            }
        )
    )

    report = service.inspect("watcher-live-bus")
    assert report["current_status"] == "COMPLETED"
    assert report["fire_count"] == 1
    assert report["events"][-1]["decision"] == "EXECUTED_AND_VERIFIED"
    assert report["action_attempted"]["executed"] is True
    assert report["independent_verification"]["state"]["state"] == "off"
    assert report["final_state"]["state"] == "off"
    assert any(
        item["decision"] == "scoped_live_grant_created"
        for item in report["authorizations"]
    )
    assert home.calls == [
        {
            "action_type": "home_assistant.turn_off",
            "target": "light.living_room_lamp",
            "parameters": {"target": "light.living_room_lamp"},
        }
    ]
    assert any(
        store.get_event(result.event_id).event_type == "snapshot_received"
        for result in results
    )
    service.stop()
    guardian.close()
    store.close()
