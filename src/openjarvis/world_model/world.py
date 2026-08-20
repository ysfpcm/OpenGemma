"""Facade connecting typed inputs to the Phase 5 world-model store."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from openjarvis.world_model.models import (
    BeliefExplanation,
    ClaimClass,
    Entity,
    EntityKind,
    InformationKind,
    MemoryLayer,
    Observation,
    Provenance,
    utc_iso,
)
from openjarvis.world_model.store import WorldModelStore


class LivingWorldModel:
    """Small application-facing world model with explicit provenance."""

    def __init__(self, store: WorldModelStore) -> None:
        self.store = store

    def entity(
        self, kind: EntityKind | str, name: str, *, aliases: tuple[str, ...] = ()
    ) -> Entity:
        return self.store.ensure_entity(kind, name, aliases=aliases)

    def observe(
        self,
        *,
        source_id: str,
        source_type: str,
        entity_kind: EntityKind | str,
        entity_name: str,
        predicate: str,
        value: Any,
        information_kind: InformationKind | str = InformationKind.OBSERVED,
        claim_class: ClaimClass | str = ClaimClass.FACT,
        confidence: float = 0.8,
        observed_at: datetime | str | None = None,
        recorded_at: datetime | str | None = None,
        evidence_text: str = "",
        provenance: Mapping[str, Any] | Provenance | None = None,
        taint_labels: tuple[str, ...] = (),
        correction: bool = False,
    ) -> Observation:
        return self.store.add_observation(
            source_id=source_id,
            source_type=source_type,
            entity_kind=entity_kind,
            entity_name=entity_name,
            predicate=predicate,
            value=value,
            information_kind=information_kind,
            claim_class=claim_class,
            confidence=confidence,
            observed_at=observed_at,
            recorded_at=recorded_at,
            evidence_text=evidence_text,
            provenance=provenance,
            taint_labels=taint_labels,
            correction=correction,
        )

    def direct_correction(
        self,
        *,
        entity_kind: EntityKind | str,
        entity_name: str,
        predicate: str,
        value: Any,
        source_id: str = "direct:marc",
        observed_at: datetime | str | None = None,
        recorded_at: datetime | str | None = None,
        evidence_text: str = "Explicit correction from Marc",
    ) -> Observation:
        """Record a trusted user correction without touching Guardian."""

        return self.observe(
            source_id=source_id,
            source_type="direct_correction",
            entity_kind=entity_kind,
            entity_name=entity_name,
            predicate=predicate,
            value=value,
            information_kind=InformationKind.REPORTED,
            claim_class=ClaimClass.FACT,
            confidence=0.99,
            observed_at=observed_at,
            recorded_at=recorded_at,
            evidence_text=evidence_text,
            provenance={
                "source_ref": source_id,
                "transformation": "explicit user correction",
            },
            correction=True,
        )

    def explain(
        self,
        *,
        entity_name: str,
        entity_kind: EntityKind | str,
        predicate: str,
        as_of: datetime | str | None = None,
    ) -> BeliefExplanation:
        entity = self.entity(entity_kind, entity_name)
        return self.store.explain(entity.entity_id, predicate, as_of=as_of)

    def ingest_durable_event(self, event: Any) -> Observation:
        """Project a typed Phase 4 event into world observations.

        Only the event envelope and typed event name are used. Arbitrary text
        in a payload remains tainted evidence and never becomes an authority
        or action request.
        """

        event_type = str(getattr(event, "event_type", "event"))
        source = str(getattr(event, "source", "phase4"))
        event_id = str(getattr(event, "event_id", ""))
        observed_at = str(getattr(event, "observed_at", utc_iso()))
        payload = dict(getattr(event, "payload", {}) or {})
        event_name = str(
            payload.get("appointment_id")
            or payload.get("route_id")
            or event_id
            or event_type
        )
        entity_kind = EntityKind.EVENT
        # The mapping is intentionally narrow. New source vocabularies must
        # add typed projection code rather than making arbitrary text live.
        predicate = {
            "calendar.appointment": "appointment_status",
            "traffic.estimate": "travel_estimate",
            "presence.home": "presence_state",
        }.get(event_type, "event_type")
        value: Any = (
            payload.get("status")
            if event_type == "calendar.appointment"
            else payload.get("present")
            if event_type == "presence.home"
            else payload.get("travel_minutes")
            if event_type == "traffic.estimate"
            else event_type
        )
        return self.observe(
            source_id=f"phase4:{source}",
            source_type="phase4_event",
            entity_kind=entity_kind,
            entity_name=event_name,
            predicate=predicate,
            value=value,
            information_kind=InformationKind.OBSERVED,
            confidence=0.95,
            observed_at=observed_at,
            recorded_at=str(getattr(event, "ingested_at", observed_at)),
            evidence_text=f"Phase 4 event {event_id or event_type}",
            provenance={
                "source_ref": event_id,
                "transformation": "typed phase4 projection",
            },
            taint_labels=tuple(getattr(event, "taint_labels", ()) or ()),
        )

    def ingest_context_event(self, event: Any) -> list[Observation]:
        """Project existing live context state without replacing ContextStore."""

        source_key = str(getattr(event, "source_key", "context"))
        entity_id = str(getattr(event, "external_entity_id", ""))
        entity_name = str(getattr(event, "entity_name", "") or entity_id or source_key)
        entity_type = str(getattr(event, "entity_type", "device"))
        try:
            kind = EntityKind(entity_type)
        except ValueError:
            kind = EntityKind.DEVICE
        state = dict(getattr(event, "state", {}) or {})
        if not state:
            state = {"event": dict(getattr(event, "payload", {}) or {})}
        result: list[Observation] = []
        for key, raw in sorted(state.items()):
            value = getattr(raw, "value", raw)
            result.append(
                self.observe(
                    source_id=f"context:{source_key}",
                    source_type="context_store",
                    entity_kind=kind,
                    entity_name=entity_name,
                    predicate=str(key),
                    value=value,
                    information_kind=InformationKind.OBSERVED,
                    confidence=0.9,
                    observed_at=getattr(event, "occurred_at", None),
                    provenance={
                        "source_ref": str(getattr(event, "external_event_id", "")),
                        "transformation": "context projection",
                    },
                )
            )
        return result


class LayeredMemory:
    """Explicit adapters over one durable typed memory table."""

    def __init__(self, store: WorldModelStore) -> None:
        self.store = store
        self.sensory_buffer = MemoryAdapter(store, MemoryLayer.SENSORY)
        self.working_memory = MemoryAdapter(store, MemoryLayer.WORKING)
        self.episodic_memory = MemoryAdapter(store, MemoryLayer.EPISODIC)
        self.semantic_memory = MemoryAdapter(store, MemoryLayer.SEMANTIC)
        self.procedural_memory = MemoryAdapter(store, MemoryLayer.PROCEDURAL)
        self.relationship_memory = MemoryAdapter(store, MemoryLayer.RELATIONSHIP)
        self.protected_self_model = ProtectedSelfModelAdapter(store)

    def adapter(self, layer: MemoryLayer | str) -> "MemoryAdapter":
        return {
            MemoryLayer.SENSORY: self.sensory_buffer,
            MemoryLayer.WORKING: self.working_memory,
            MemoryLayer.EPISODIC: self.episodic_memory,
            MemoryLayer.SEMANTIC: self.semantic_memory,
            MemoryLayer.PROCEDURAL: self.procedural_memory,
            MemoryLayer.RELATIONSHIP: self.relationship_memory,
            MemoryLayer.SELF_MODEL: self.protected_self_model,
        }[MemoryLayer(layer.value if isinstance(layer, MemoryLayer) else str(layer))]


class MemoryAdapter:
    """A named adapter for one layer; it stores no authority or actions."""

    def __init__(self, store: WorldModelStore, layer: MemoryLayer) -> None:
        self.store = store
        self.layer = layer

    def write(
        self,
        content: str,
        *,
        source_id: str,
        evidence_ids: tuple[str, ...] = (),
        confidence: float = 0.5,
        created_at: datetime | str | None = None,
        expires_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        trusted: bool = False,
    ):
        return self.store.add_memory_item(
            layer=self.layer,
            content=content,
            source_id=source_id,
            evidence_ids=evidence_ids,
            confidence=confidence,
            created_at=created_at,
            expires_at=expires_at,
            metadata=metadata,
        )

    def read(self, *, query: str = "", limit: int = 50):
        return self.store.memory_items(layer=self.layer, query=query, limit=limit)


class ProtectedSelfModelAdapter(MemoryAdapter):
    """Self-model writes require an explicit trusted internal caller."""

    def __init__(self, store: WorldModelStore) -> None:
        super().__init__(store, MemoryLayer.SELF_MODEL)

    def write(
        self,
        content: str,
        *,
        source_id: str,
        evidence_ids: tuple[str, ...] = (),
        confidence: float = 0.5,
        created_at: datetime | str | None = None,
        expires_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        trusted: bool = False,
    ):
        if not trusted:
            raise PermissionError(
                "protected self-model requires an explicit trusted write"
            )
        return super().write(
            content,
            source_id=source_id,
            evidence_ids=evidence_ids,
            confidence=confidence,
            created_at=created_at,
            expires_at=expires_at,
            metadata=metadata,
            trusted=trusted,
        )


class ExistingSourceAdapters:
    """Non-destructive projections for the existing context families.

    These adapters intentionally accept duck-typed records so the established
    context, fact, retrieval, session, behavior, and trace stores do not need
    a synchronized migration. They copy only the typed fields useful to the
    Phase 5 continuity layer and preserve the originating source label.
    """

    def __init__(self, world: LivingWorldModel, memory: LayeredMemory) -> None:
        self.world = world
        self.memory = memory

    def context_event(self, event: Any) -> list[Observation]:
        return self.world.ingest_context_event(event)

    def durable_event(self, event: Any) -> Observation:
        return self.world.ingest_durable_event(event)

    def fact(
        self,
        text: str,
        *,
        source_id: str = "existing:fact-store",
        created_at: datetime | str | None = None,
    ):
        return self.memory.semantic_memory.write(
            text,
            source_id=source_id,
            created_at=created_at,
            metadata={"adapter": "fact_store"},
        )

    def retrieval_result(
        self,
        result: Any,
        *,
        source_id: str = "existing:rag",
        created_at: datetime | str | None = None,
    ):
        content = str(getattr(result, "content", result))
        source = str(getattr(result, "source", ""))
        return self.memory.semantic_memory.write(
            content,
            source_id=source_id,
            created_at=created_at,
            metadata={"adapter": "rag", "retrieval_source": source},
        )

    def session_message(
        self, session_id: str, content: str, *, created_at: datetime | str | None = None
    ):
        return self.memory.episodic_memory.write(
            content,
            source_id=f"session:{session_id}",
            created_at=created_at,
            metadata={"adapter": "session"},
        )

    def behavior_example(
        self,
        content: str,
        *,
        source_id: str = "existing:behavior",
        created_at: datetime | str | None = None,
    ):
        return self.memory.procedural_memory.write(
            content,
            source_id=source_id,
            created_at=created_at,
            metadata={"adapter": "behavior"},
        )

    def trace(
        self, content: str, *, trace_id: str, created_at: datetime | str | None = None
    ):
        return self.memory.episodic_memory.write(
            content,
            source_id=f"trace:{trace_id}",
            created_at=created_at,
            metadata={"adapter": "trace"},
        )


WorldModel = LivingWorldModel


__all__ = [
    "ExistingSourceAdapters",
    "LayeredMemory",
    "LivingWorldModel",
    "MemoryAdapter",
    "ProtectedSelfModelAdapter",
    "WorldModel",
]
