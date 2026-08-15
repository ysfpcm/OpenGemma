"""Adapters from Ophanim live-context snapshots to behavior entities."""

from __future__ import annotations

from typing import Any

from openjarvis.behavior.models import BehaviorEntity


def entities_from_snapshot(snapshot: Any) -> tuple[BehaviorEntity, ...]:
    """Convert a ``ContextSnapshot`` into canonical entities for the resolver."""
    result: list[BehaviorEntity] = []
    for item in getattr(snapshot, "entities", ()):
        external_id = str(getattr(item, "external_entity_id", "") or "").strip()
        name = str(getattr(item, "entity_name", "") or "").strip()
        if not external_id or "." not in external_id or not name:
            continue
        result.append(
            BehaviorEntity(
                entity_id=external_id,
                name=name,
                domain=str(getattr(item, "entity_type", "") or ""),
                area=str(getattr(item, "area", "") or ""),
            )
        )
    return tuple(result)


__all__ = ["entities_from_snapshot"]
