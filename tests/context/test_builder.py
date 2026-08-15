from __future__ import annotations

from pathlib import Path

from openjarvis.context import ContextBuilder, ContextEvent, ContextRequest, ContextStore, StateValue


def _event(
    *,
    event_id: str,
    entity_id: str,
    entity_type: str,
    name: str,
    area: str,
    occurred_at: str,
    state: dict[str, object],
    source_status: str = "online",
    stale_after_seconds: int = 300,
) -> ContextEvent:
    return ContextEvent(
        source_key="home_assistant",
        source_type="home_assistant",
        source_display_name="Home Assistant",
        source_status=source_status,
        stale_after_seconds=stale_after_seconds,
        external_event_id=event_id,
        external_entity_id=entity_id,
        entity_type=entity_type,
        entity_name=name,
        area=area,
        occurred_at=occurred_at,
        state=state,
    )


def _store(tmp_path: Path) -> ContextStore:
    return ContextStore(tmp_path / "context.db")


def test_builder_groups_home_state_and_renders_compact_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.apply_event(
        _event(
            event_id="light-1",
            entity_id="light.living_room",
            entity_type="light",
            name="Living Room Light",
            area="living_room",
            occurred_at="2026-08-08T10:00:00Z",
            state={"power": "on", "brightness": StateValue(80, "%")},
        )
    )
    store.apply_event(
        _event(
            event_id="temp-1",
            entity_id="sensor.living_room_temperature",
            entity_type="sensor",
            name="Living Room Temperature",
            area="living_room",
            occurred_at="2026-08-08T10:00:00Z",
            state={"temperature": StateValue(73, "F")},
        )
    )
    store.apply_event(
        _event(
            event_id="door-1",
            entity_id="binary_sensor.front_door",
            entity_type="door",
            name="Front Door",
            area="entry",
            occurred_at="2026-08-08T10:00:00Z",
            state={"open": False},
        )
    )

    snapshot = ContextBuilder(store).build(
        ContextRequest(now="2026-08-08T10:01:00Z", recent_event_limit=5)
    )
    assert [entity.entity_name for entity in snapshot.entities] == [
        "Front Door",
        "Living Room Light",
        "Living Room Temperature",
    ]
    light = snapshot.entities[1]
    assert [(state.state_key, state.value, state.unit) for state in light.states] == [
        ("brightness", 80, "%"),
        ("power", "on", ""),
    ]
    assert not snapshot.warnings
    text = snapshot.to_prompt_text()
    assert "Living Room Light" in text
    assert "brightness: 80 %" in text
    assert "No recent events" not in text
    store.close()


def test_builder_filters_by_area_and_entity_type(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.apply_event(
        _event(
            event_id="light-1",
            entity_id="light.living_room",
            entity_type="light",
            name="Living Room Light",
            area="living_room",
            occurred_at="2026-08-08T10:00:00Z",
            state={"power": "on"},
        )
    )
    store.apply_event(
        _event(
            event_id="tv-1",
            entity_id="media_player.tv",
            entity_type="media_player",
            name="Living Room TV",
            area="living_room",
            occurred_at="2026-08-08T10:00:00Z",
            state={"power": "on"},
        )
    )

    snapshot = ContextBuilder(store).build(
        ContextRequest(
            areas=("living_room",),
            entity_types=("light",),
            recent_event_limit=10,
            now="2026-08-08T10:00:30Z",
        )
    )
    assert [entity.entity_name for entity in snapshot.entities] == ["Living Room Light"]
    assert [event.entity_name for event in snapshot.recent_events] == ["Living Room Light"]
    store.close()


def test_builder_marks_stale_state_and_offline_sources(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.apply_event(
        _event(
            event_id="motion-1",
            entity_id="camera.back_door",
            entity_type="camera",
            name="Back Door Camera",
            area="back_door",
            occurred_at="2026-08-08T09:00:00Z",
            state={"motion": True},
            source_status="offline",
            stale_after_seconds=60,
        )
    )

    snapshot = ContextBuilder(store).build(
        ContextRequest(now="2026-08-08T10:00:00Z", recent_event_limit=0)
    )
    assert snapshot.entities[0].stale is True
    assert any("offline" in warning for warning in snapshot.warnings)
    assert any("stale" in warning for warning in snapshot.warnings)
    assert snapshot.recent_events == ()
    store.close()


def test_snapshot_json_is_deterministic_and_contains_warnings(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.apply_event(
        _event(
            event_id="presence-1",
            entity_id="person.marc",
            entity_type="person",
            name="Marc",
            area="home",
            occurred_at="2026-08-08T10:00:00Z",
            state={"presence": "home"},
        )
    )
    snapshot = ContextBuilder(store).build(
        ContextRequest(now="2026-08-08T10:00:01Z", recent_event_limit=1)
    )
    first = snapshot.to_json(indent=None)
    second = snapshot.to_json(indent=None)
    assert first == second
    assert '"generated_at":"2026-08-08T10:00:01.000Z"' in first
    assert snapshot.render() == snapshot.to_prompt_text()
    store.close()
