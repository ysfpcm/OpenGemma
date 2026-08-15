from __future__ import annotations

import asyncio

import pytest

from openjarvis.context import (
    CallableCollector,
    ContextBuilder,
    ContextEvent,
    ContextStore,
    ContextSyncService,
    StateValue,
    TrafficContextCollector,
    TrafficSnapshotFileCollector,
)
from openjarvis.tools._stubs import ToolResult


@pytest.mark.asyncio
async def test_sync_service_refreshes_context_without_an_agent(tmp_path):
    store = ContextStore(tmp_path / "context.db")
    calls = 0

    async def collect():
        nonlocal calls
        calls += 1
        return (
            ContextEvent(
                source_key="mail",
                source_type="email",
                external_event_id=f"mail-{calls}",
                external_entity_id="inbox",
                entity_type="inbox",
                entity_name="Inbox",
                stale_after_seconds=60,
                state={"unread": StateValue(calls)},
            ),
        )

    service = ContextSyncService(
        store,
        [CallableCollector("mail", 0.01, collect)],
    )
    service.start()
    await asyncio.sleep(0.035)
    await service.stop()

    snapshot = ContextBuilder(store).build()
    assert calls >= 2
    assert snapshot.entities[0].states[0].state_key == "unread"
    assert snapshot.entities[0].states[0].value == calls
    store.close()


@pytest.mark.asyncio
async def test_traffic_collector_normalizes_structured_result(monkeypatch):
    from openjarvis.tools.traffic_tool import TrafficTool

    def execute(_self, **_kwargs):
        return ToolResult(
            tool_name="traffic_lookup",
            content="ok",
            success=True,
            metadata={
                "provider": "test",
                "normal_duration_seconds": 1200,
                "traffic_duration_seconds": 1800,
            },
        )

    monkeypatch.setattr(TrafficTool, "execute", execute)
    collector = TrafficContextCollector(
        origin="Home",
        destination="Fort Carson",
        route_name="home_to_carson",
        interval_seconds=120,
    )

    events = await collector.collect()

    assert len(events) == 1
    assert events[0].external_entity_id == "home_to_carson"
    assert events[0].state["delay_seconds"].value == 600
    assert events[0].stale_after_seconds == 240


@pytest.mark.asyncio
async def test_sync_service_survives_one_failed_refresh(tmp_path):
    store = ContextStore(tmp_path / "context.db")
    attempts = 0

    async def flaky_collect():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return (
            ContextEvent(
                source_key="calendar",
                external_event_id="calendar-1",
                external_entity_id="today",
                entity_type="calendar",
                entity_name="Calendar",
                state={"next_event": "Meeting"},
            ),
        )

    service = ContextSyncService(
        store,
        [CallableCollector("calendar", 0.01, flaky_collect)],
    )
    service.start()
    await asyncio.sleep(0.03)
    await service.stop()

    assert attempts >= 2
    assert ContextBuilder(store).build().entities
    store.close()


@pytest.mark.asyncio
async def test_traffic_snapshot_file_collector_imports_existing_feed(tmp_path):
    snapshot = tmp_path / "traffic.json"
    snapshot.write_text(
        '{"updated_at_utc":"2026-08-09T20:00:00Z",'
        '"origin":"Home","destination":"Fort Carson",'
        '"normal_duration":"1 hour 5 mins",'
        '"traffic_duration":"1 hour 20 mins","source":"Maps"}',
        encoding="utf-8",
    )
    collector = TrafficSnapshotFileCollector(snapshot, interval_seconds=30)

    events = await collector.collect()

    assert events[0].state["normal_duration_seconds"].value == 3900
    assert events[0].state["traffic_duration_seconds"].value == 4800
    assert events[0].state["delay_seconds"].value == 900
