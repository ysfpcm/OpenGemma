"""Lightweight background refreshers for Ophanim's local context memory.

Collectors fetch facts; they never invoke an LLM.  The sync service schedules
each collector independently and persists normalized events in ``ContextStore``.
This keeps external I/O off the interactive chat path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol, Sequence
from pathlib import Path

from openjarvis.context.store import ContextEvent, ContextStore, StateValue

logger = logging.getLogger(__name__)


class ContextCollector(Protocol):
    """A source that can produce normalized context events without an LLM."""

    collector_id: str
    interval_seconds: float

    async def collect(self) -> Sequence[ContextEvent]: ...


@dataclass(slots=True)
class CallableCollector:
    """Adapter for application-specific async collector functions."""

    collector_id: str
    interval_seconds: float
    callback: Callable[[], Awaitable[Sequence[ContextEvent]]]

    async def collect(self) -> Sequence[ContextEvent]:
        return await self.callback()


class TrafficContextCollector:
    """Refresh one commute route through the existing traffic tool."""

    collector_id = "traffic"

    def __init__(
        self,
        *,
        origin: str,
        destination: str,
        route_name: str = "commute",
        interval_seconds: float = 300,
    ) -> None:
        if not origin.strip() or not destination.strip():
            raise ValueError("traffic origin and destination are required")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero")
        self.origin = origin.strip()
        self.destination = destination.strip()
        self.route_name = route_name.strip() or "commute"
        self.interval_seconds = float(interval_seconds)

    async def collect(self) -> Sequence[ContextEvent]:
        from openjarvis.tools.traffic_tool import TrafficTool

        result = await asyncio.to_thread(
            TrafficTool().execute,
            origin=self.origin,
            destination=self.destination,
        )
        if not result.success:
            raise RuntimeError("traffic provider did not return a usable result")

        metadata = result.metadata or {}
        normal = metadata.get("normal_duration_seconds")
        traffic = metadata.get("traffic_duration_seconds")
        if not isinstance(normal, (int, float)) or not isinstance(
            traffic, (int, float)
        ):
            raise RuntimeError("traffic provider omitted structured durations")

        observed_at = datetime.now(timezone.utc)
        delay = max(0, int(traffic) - int(normal))
        return (
            ContextEvent(
                source_key="traffic",
                source_type="traffic",
                source_display_name="Traffic",
                source_status="online",
                stale_after_seconds=max(1, int(self.interval_seconds * 2)),
                external_event_id=f"{self.route_name}:{observed_at.isoformat()}",
                external_entity_id=self.route_name,
                entity_type="commute_route",
                entity_name=self.route_name,
                event_type="traffic_refreshed",
                occurred_at=observed_at,
                state={
                    "origin": StateValue(self.origin),
                    "destination": StateValue(self.destination),
                    "normal_duration_seconds": StateValue(int(normal), unit="s"),
                    "traffic_duration_seconds": StateValue(int(traffic), unit="s"),
                    "delay_seconds": StateValue(delay, unit="s"),
                },
                payload={"provider": metadata.get("provider", "traffic_lookup")},
            ),
        )


class TrafficSnapshotFileCollector:
    """Import traffic snapshots produced by an external scheduled refresher."""

    collector_id = "traffic_snapshot_file"

    def __init__(self, path: str | Path, *, interval_seconds: float = 30) -> None:
        self.path = Path(path).expanduser()
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero")
        self.interval_seconds = float(interval_seconds)

    async def collect(self) -> Sequence[ContextEvent]:
        data = await asyncio.to_thread(self._read)
        observed_at = data.get("updated_at_utc")
        if not isinstance(observed_at, str) or not observed_at.strip():
            observed_at = datetime.now(timezone.utc).isoformat()
        origin = str(data.get("origin", "")).strip()
        destination = str(data.get("destination", "")).strip()
        normal_text = str(data.get("normal_duration", "")).strip()
        traffic_text = str(data.get("traffic_duration", "")).strip()
        normal_seconds = self._duration_seconds(normal_text)
        traffic_seconds = self._duration_seconds(traffic_text)
        state = {
            "origin": StateValue(origin),
            "destination": StateValue(destination),
            "normal_duration": StateValue(normal_text),
            "traffic_duration": StateValue(traffic_text),
        }
        if normal_seconds is not None and traffic_seconds is not None:
            state.update(
                {
                    "normal_duration_seconds": StateValue(normal_seconds, unit="s"),
                    "traffic_duration_seconds": StateValue(traffic_seconds, unit="s"),
                    "delay_seconds": StateValue(
                        max(0, traffic_seconds - normal_seconds), unit="s"
                    ),
                }
            )
        return (
            ContextEvent(
                source_key="traffic",
                source_type="traffic",
                source_display_name="Traffic",
                stale_after_seconds=max(1, int(self.interval_seconds * 4)),
                external_event_id=f"commute:{observed_at}",
                external_entity_id="commute",
                entity_type="commute_route",
                entity_name="Commute",
                event_type="traffic_snapshot_imported",
                occurred_at=observed_at,
                state=state,
                payload={"provider": str(data.get("source", "snapshot_file"))},
            ),
        )

    def _read(self) -> dict:
        with self.path.open("r", encoding="utf-8-sig") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("traffic snapshot must contain a JSON object")
        return value

    @staticmethod
    def _duration_seconds(value: str) -> int | None:
        hours = re.search(r"(\d+)\s*(?:hours?|hrs?)", value, re.IGNORECASE)
        minutes = re.search(r"(\d+)\s*(?:minutes?|mins?)", value, re.IGNORECASE)
        if not hours and not minutes:
            return None
        hour_seconds = int(hours.group(1)) * 3600 if hours else 0
        minute_seconds = int(minutes.group(1)) * 60 if minutes else 0
        return hour_seconds + minute_seconds


class ContextSyncService:
    """Run independent collectors continuously and save their latest facts."""

    def __init__(
        self,
        store: ContextStore,
        collectors: Sequence[ContextCollector] = (),
        *,
        run_immediately: bool = True,
    ) -> None:
        self.store = store
        self.collectors = tuple(collectors)
        self.run_immediately = run_immediately
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def running(self) -> bool:
        return bool(self._tasks) and any(not task.done() for task in self._tasks)

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(
                self._run_collector(collector),
                name=f"context-sync-{collector.collector_id}",
            )
            for collector in self.collectors
        ]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def refresh(self, collector: ContextCollector) -> int:
        events = await collector.collect()
        for event in events:
            self.store.apply_event(event)
        return len(events)

    async def _run_collector(self, collector: ContextCollector) -> None:
        interval = float(collector.interval_seconds)
        if interval <= 0:
            logger.error("Context collector %s has an invalid interval", collector.collector_id)
            return
        first = True
        while not self._stop.is_set():
            if self.run_immediately or not first:
                try:
                    await self.refresh(collector)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "Context collector %s refresh failed",
                        collector.collector_id,
                        exc_info=True,
                    )
            first = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass


__all__ = [
    "CallableCollector",
    "ContextCollector",
    "ContextSyncService",
    "TrafficContextCollector",
    "TrafficSnapshotFileCollector",
]
