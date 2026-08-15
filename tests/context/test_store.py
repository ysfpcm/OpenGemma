from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openjarvis.context import ContextEvent, ContextStore, StateValue


def _event(
    *,
    event_id: str,
    occurred_at: str,
    state: dict[str, object] | None = None,
    source_status: str = "online",
    stale_after_seconds: int | None = 60,
    external_entity_id: str = "light.living_room",
    entity_type: str = "light",
    entity_name: str = "Living Room Light",
    area: str = "living_room",
) -> ContextEvent:
    return ContextEvent(
        source_key="home_assistant",
        source_type="home_assistant",
        source_display_name="Home Assistant",
        source_status=source_status,
        stale_after_seconds=stale_after_seconds,
        external_event_id=event_id,
        external_entity_id=external_entity_id,
        entity_type=entity_type,
        entity_name=entity_name,
        area=area,
        occurred_at=occurred_at,
        state=state or {},
        payload={"origin": "test"},
    )


def _store(tmp_path: Path) -> ContextStore:
    return ContextStore(tmp_path / "context.db")


def test_schema_initializes_and_enforces_foreign_keys(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tables = {
        row[0]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "context_sources",
        "context_entities",
        "context_events",
        "context_current_state",
        "context_state_history",
    } <= tables

    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            """
            INSERT INTO context_entities
                (source_id, external_id, entity_type, display_name)
            VALUES (999, 'missing', 'sensor', 'Missing')
            """
        )
    store.close()


def test_source_and_entity_upserts_are_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.apply_event(
        _event(event_id="1", occurred_at="2026-08-08T10:00:00Z", state={"power": "on"})
    )
    second = store.apply_event(
        _event(event_id="2", occurred_at="2026-08-08T10:01:00Z", state={"power": "off"})
    )

    assert first.entity_id == second.entity_id
    assert store._conn.execute("SELECT COUNT(*) FROM context_sources").fetchone()[0] == 1
    assert store._conn.execute("SELECT COUNT(*) FROM context_entities").fetchone()[0] == 1
    assert store._conn.execute("SELECT COUNT(*) FROM context_events").fetchone()[0] == 2
    store.close()


def test_multiple_properties_and_history_are_projected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = store.apply_event(
        _event(
            event_id="1",
            occurred_at="2026-08-08T10:00:00Z",
            state={"power": "on", "brightness": StateValue(80, "%")},
        )
    )

    current = store.get_current_state()
    assert result.changed_state_keys == ("brightness", "power")
    assert {(row.state_key, row.value, row.unit) for row in current} == {
        ("brightness", 80, "%"),
        ("power", "on", ""),
    }
    history = store.get_history(result.entity_id)
    assert [row["state_key"] for row in history] == ["power", "brightness"]
    assert all(row["previous_value"] is None for row in history)
    store.close()


def test_duplicate_event_does_not_duplicate_history_or_current_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event = _event(
        event_id="same",
        occurred_at="2026-08-08T10:00:00Z",
        state={"power": "on"},
        source_status="offline",
    )
    first = store.apply_event(event)
    duplicate = store.apply_event(
        ContextEvent(
            source_key="home_assistant",
            external_event_id="same",
            external_entity_id="light.living_room",
            occurred_at="2026-08-08T10:00:00Z",
            state={"power": "on"},
        )
    )

    assert first.inserted is True
    assert duplicate.duplicate is True
    assert duplicate.event_id == first.event_id
    assert store._conn.execute("SELECT COUNT(*) FROM context_events").fetchone()[0] == 1
    assert store._conn.execute("SELECT COUNT(*) FROM context_state_history").fetchone()[0] == 1
    assert store._conn.execute("SELECT status FROM context_sources").fetchone()[0] == "offline"
    store.close()


def test_unchanged_state_refreshes_observation_without_new_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.apply_event(
        _event(event_id="1", occurred_at="2026-08-08T10:00:00Z", state={"power": "on"})
    )
    second = store.apply_event(
        _event(event_id="2", occurred_at="2026-08-08T10:01:00Z", state={"power": "on"})
    )

    assert second.changed_state_keys == ()
    assert second.updated_state_keys == ("power",)
    assert store._conn.execute("SELECT COUNT(*) FROM context_state_history").fetchone()[0] == 1
    current = store.get_current_state()
    assert current[0].observed_at == "2026-08-08T10:01:00.000Z"
    assert first.entity_id == second.entity_id
    store.close()


def test_out_of_order_event_is_retained_but_cannot_regress_current_state(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    latest = store.apply_event(
        _event(event_id="latest", occurred_at="2026-08-08T10:02:00Z", state={"power": "off"})
    )
    old = store.apply_event(
        _event(event_id="old", occurred_at="2026-08-08T10:01:00Z", state={"power": "on"})
    )

    assert old.inserted is True
    assert store.get_current_state()[0].value == "off"
    assert store._conn.execute("SELECT COUNT(*) FROM context_events").fetchone()[0] == 2
    assert len(store.get_history(latest.entity_id)) == 1
    store.close()


def test_event_and_history_ordering(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entity_id = store.apply_event(
        _event(event_id="1", occurred_at="2026-08-08T10:00:00Z", state={"power": "off"})
    ).entity_id
    store.apply_event(
        _event(event_id="2", occurred_at="2026-08-08T10:05:00Z", state={"power": "on"})
    )
    store.apply_event(
        _event(event_id="3", occurred_at="2026-08-08T10:10:00Z", state={"power": "off"})
    )

    history = store.get_history(entity_id, limit=2)
    assert [row["new_value"] for row in history] == ["off", "on"]
    recent = store.get_recent_events(limit=2)
    assert [event.event_id for event in recent] == [3, 2]
    store.close()


def test_source_level_events_are_supported(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = store.apply_event(
        ContextEvent(
            source_key="network",
            source_type="network_monitor",
            source_display_name="Network Monitor",
            event_type="source_status",
            external_event_id="network-1",
            occurred_at="2026-08-08T10:00:00Z",
            payload={"connected_devices": 12},
        )
    )

    assert result.entity_id is None
    events = store.get_recent_events()
    assert events[0].entity_name is None
    assert events[0].payload == {"connected_devices": 12}
    store.close()


def test_invalid_event_rolls_back_source_entity_and_event(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="event_type"):
        store.apply_event(
            ContextEvent(
                source_key="home_assistant",
                source_type="home_assistant",
                source_display_name="Home Assistant",
                external_event_id="invalid",
                external_entity_id="light.living_room",
                entity_type="light",
                entity_name="Living Room Light",
                event_type="",
                occurred_at="2026-08-08T10:00:00Z",
                state={"power": "on"},
            )
        )

    assert store._conn.execute("SELECT COUNT(*) FROM context_sources").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM context_entities").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM context_events").fetchone()[0] == 0
    store.close()


def test_timestamp_normalization_accepts_datetime() -> None:
    event = ContextEvent(
        source_key="test",
        occurred_at=datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc),
    )
    assert event.occurred_at is not None
