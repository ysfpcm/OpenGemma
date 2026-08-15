"""Build compact, model-facing snapshots from the live context store."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from openjarvis.context.store import (
    ContextStore,
    CurrentStateRecord,
    RecentEventRecord,
    SourceHealth,
)


def _as_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """Selectors and limits for one context build."""

    source_keys: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    entity_types: tuple[str, ...] = ()
    include_disabled: bool = False
    recent_event_limit: int = 20
    now: datetime | str | None = None


@dataclass(frozen=True, slots=True)
class SnapshotState:
    state_key: str
    value: Any
    unit: str
    quality: str
    observed_at: str


@dataclass(frozen=True, slots=True)
class SnapshotEntity:
    entity_id: int
    source_key: str
    source_display_name: str
    external_entity_id: str
    entity_type: str
    entity_name: str
    area: str
    stale: bool
    states: tuple[SnapshotState, ...] = ()


@dataclass(frozen=True, slots=True)
class SnapshotEvent:
    event_id: int
    source_key: str
    entity_name: str | None
    area: str | None
    event_type: str
    occurred_at: str
    payload: Any


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Immutable context result with deterministic JSON and text renderings."""

    generated_at: str
    entities: tuple[SnapshotEntity, ...] = ()
    recent_events: tuple[SnapshotEvent, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "entities": [
                {
                    "entity_id": entity.entity_id,
                    "source_key": entity.source_key,
                    "source_display_name": entity.source_display_name,
                    "external_entity_id": entity.external_entity_id,
                    "entity_type": entity.entity_type,
                    "entity_name": entity.entity_name,
                    "area": entity.area,
                    "stale": entity.stale,
                    "states": [
                        {
                            "state_key": state.state_key,
                            "value": state.value,
                            "unit": state.unit,
                            "quality": state.quality,
                            "observed_at": state.observed_at,
                        }
                        for state in entity.states
                    ],
                }
                for entity in self.entities
            ],
            "recent_events": [
                {
                    "event_id": event.event_id,
                    "source_key": event.source_key,
                    "entity_name": event.entity_name,
                    "area": event.area,
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at,
                    "payload": event.payload,
                }
                for event in self.recent_events
            ],
            "warnings": list(self.warnings),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Return a stable JSON representation suitable for an API boundary."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
            separators=None if indent is not None else (",", ":"),
        )

    def to_prompt_text(self) -> str:
        """Render a compact, readable snapshot for a local intent model."""
        lines = [
            "## Current Context",
            f"Generated at: {self.generated_at}",
            "",
            "### Current State",
        ]

        if self.entities:
            for entity in self.entities:
                location = f" [{entity.area}]" if entity.area else ""
                stale_tag = " (stale)" if entity.stale else ""
                lines.append(
                    f"- {entity.entity_name}{location} "
                    f"({entity.entity_type}, source={entity.source_key}){stale_tag}"
                )
                for state in entity.states:
                    unit = f" {state.unit}" if state.unit else ""
                    quality = f", quality={state.quality}" if state.quality != "good" else ""
                    lines.append(
                        f"  - {state.state_key}: {_format_value(state.value)}{unit} "
                        f"(observed {state.observed_at}{quality})"
                    )
        else:
            lines.append("- No current entity state matched the request.")

        lines.extend(["", "### Recent Events"])
        if self.recent_events:
            for event in self.recent_events:
                subject = event.entity_name or event.source_key
                payload = _format_value(event.payload)
                lines.append(
                    f"- {event.occurred_at} — {subject}: {event.event_type} ({payload})"
                )
        else:
            lines.append("- No recent events matched the request.")

        lines.extend(["", "### Warnings"])
        if self.warnings:
            lines.extend(f"- {warning}" for warning in self.warnings)
        else:
            lines.append("- None")
        return "\n".join(lines)

    render = to_prompt_text


class ContextBuilder:
    """Read-only facade that assembles model-ready snapshots."""

    def __init__(self, store: ContextStore) -> None:
        self._store = store

    def build(self, request: ContextRequest | None = None) -> ContextSnapshot:
        request = request or ContextRequest()
        if request.recent_event_limit < 0:
            raise ValueError("recent_event_limit must be non-negative")

        now = _as_datetime(request.now)
        generated_at = _format_timestamp(now)
        source_keys = tuple(request.source_keys)
        areas = tuple(request.areas)
        entity_types = tuple(request.entity_types)

        rows = self._store.get_current_state(
            source_keys=source_keys,
            areas=areas,
            entity_types=entity_types,
            include_disabled=request.include_disabled,
        )
        health = self._store.get_source_health(source_keys=source_keys)
        events = self._store.get_recent_events(
            limit=request.recent_event_limit,
            source_keys=source_keys,
            areas=areas,
            entity_types=entity_types,
            include_disabled=request.include_disabled,
        )

        entities = self._group_entities(rows, now)
        warnings = self._build_warnings(health, entities, now)
        return ContextSnapshot(
            generated_at=generated_at,
            entities=entities,
            recent_events=tuple(self._event_snapshot(event) for event in events),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _group_entities(
        rows: Sequence[CurrentStateRecord],
        now: datetime,
    ) -> tuple[SnapshotEntity, ...]:
        grouped: dict[int, list[CurrentStateRecord]] = {}
        for row in rows:
            grouped.setdefault(row.entity_id, []).append(row)

        entities: list[SnapshotEntity] = []
        # ``rows`` already arrive in area/name order from ContextStore. Dicts
        # preserve first-seen order, so keep that human-readable ordering
        # instead of falling back to insertion IDs.
        for entity_rows in grouped.values():
            first = entity_rows[0]
            latest_observed = max(_as_datetime(row.observed_at) for row in entity_rows)
            stale = (
                first.source_status == "offline"
                or (now - latest_observed).total_seconds() > first.stale_after_seconds
            )
            states = tuple(
                SnapshotState(
                    state_key=row.state_key,
                    value=row.value,
                    unit=row.unit,
                    quality=row.quality,
                    observed_at=row.observed_at,
                )
                for row in sorted(entity_rows, key=lambda item: item.state_key)
            )
            entities.append(
                SnapshotEntity(
                    entity_id=first.entity_id,
                    source_key=first.source_key,
                    source_display_name=first.source_display_name,
                    external_entity_id=first.external_entity_id,
                    entity_type=first.entity_type,
                    entity_name=first.entity_name,
                    area=first.area,
                    stale=stale,
                    states=states,
                )
            )
        return tuple(entities)

    @staticmethod
    def _build_warnings(
        health: Sequence[SourceHealth],
        entities: Sequence[SnapshotEntity],
        now: datetime,
    ) -> list[str]:
        warnings: list[str] = []
        for source in health:
            label = f"{source.display_name} ({source.source_key})"
            if source.status == "offline":
                warnings.append(f"{label} is offline.")
            elif source.status == "degraded":
                warnings.append(f"{label} is degraded.")

            if source.last_seen_at is None:
                warnings.append(f"{label} has not reported yet.")
            elif (
                now - _as_datetime(source.last_seen_at)
            ).total_seconds() > source.stale_after_seconds:
                warnings.append(
                    f"{label} data is stale; last seen at {source.last_seen_at}."
                )
        for entity in entities:
            if entity.stale:
                warnings.append(f"{entity.entity_name} state is stale.")
        return warnings

    @staticmethod
    def _event_snapshot(event: RecentEventRecord) -> SnapshotEvent:
        return SnapshotEvent(
            event_id=event.event_id,
            source_key=event.source_key,
            entity_name=event.entity_name,
            area=event.area,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            payload=event.payload,
        )


__all__ = [
    "ContextBuilder",
    "ContextRequest",
    "ContextSnapshot",
    "SnapshotEntity",
    "SnapshotEvent",
    "SnapshotState",
]
