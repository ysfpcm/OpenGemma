"""Minimal read-only inspection API for the Phase 5 world model."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openjarvis.core.paths import get_config_dir
from openjarvis.world_model import (
    ClaimClass,
    EntityKind,
    InformationKind,
    LayeredMemory,
    LivingWorldModel,
    WorldModelStore,
)

router = APIRouter(prefix="/v1/world", tags=["world-model"])


class ObservationRequest(BaseModel):
    source_id: str = Field(min_length=1)
    source_type: str = Field(default="api", min_length=1)
    entity_kind: EntityKind = EntityKind.PERSON
    entity_name: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: Any
    information_kind: InformationKind = InformationKind.REPORTED
    claim_class: ClaimClass = ClaimClass.FACT
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    observed_at: datetime | None = None
    recorded_at: datetime | None = None
    evidence_text: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    taint_labels: list[str] = Field(default_factory=list)
    correction: bool = False


def _store(request: Request) -> WorldModelStore:
    store = getattr(request.app.state, "phase5_store", None)
    if store is None:
        raise HTTPException(503, "Phase 5 world model is disabled")
    return store


@router.get("/beliefs")
async def list_beliefs(
    request: Request,
    entity_id: str | None = None,
    predicate: str | None = None,
    include_hypotheses: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    store = _store(request)
    return {
        "beliefs": [
            item.to_dict()
            for item in store.beliefs(
                entity_id=entity_id,
                predicate=predicate,
                include_hypotheses=include_hypotheses,
                limit=limit,
            )
        ],
        "side_effects": False,
    }


@router.get("/beliefs/explain")
async def explain_belief(
    request: Request,
    entity_id: str,
    predicate: str,
    as_of: str | None = None,
) -> dict[str, Any]:
    store = _store(request)
    return {
        "explanation": store.explain(entity_id, predicate, as_of=as_of).to_dict(),
        "side_effects": False,
    }


@router.get("/reconstruct")
async def reconstruct_beliefs(request: Request, as_of: str) -> dict[str, Any]:
    store = _store(request)
    return {
        "as_of": as_of,
        "beliefs": [item.to_dict() for item in store.beliefs_at(as_of)],
        "side_effects": False,
    }


@router.get("/entities")
async def list_entities(request: Request, limit: int = 100) -> dict[str, Any]:
    store = _store(request)
    return {
        "entities": [item.to_dict() for item in store.entities(limit=limit)],
        "side_effects": False,
    }


@router.get("/sources")
async def list_sources(
    request: Request, include_deleted: bool = False
) -> dict[str, Any]:
    store = _store(request)
    return {
        "sources": [
            item.to_dict() for item in store.sources(include_deleted=include_deleted)
        ],
        "side_effects": False,
    }


@router.get("/memory")
async def list_memory(
    request: Request, layer: str | None = None, query: str = "", limit: int = 50
) -> dict[str, Any]:
    store = _store(request)
    return {
        "memory": [
            item.to_dict()
            for item in store.memory_items(layer=layer, query=query, limit=limit)
        ],
        "side_effects": False,
    }


@router.get("/goals")
async def list_goals(request: Request, entity_id: str | None = None) -> dict[str, Any]:
    store = _store(request)
    return {
        "goals": [item.to_dict() for item in store.goals(entity_id=entity_id)],
        "side_effects": False,
    }


@router.get("/commitments")
async def list_commitments(
    request: Request, entity_id: str | None = None
) -> dict[str, Any]:
    store = _store(request)
    return {
        "commitments": [
            item.to_dict() for item in store.commitments(entity_id=entity_id)
        ],
        "side_effects": False,
    }


@router.get("/predictions/{prediction_id}")
async def get_prediction(request: Request, prediction_id: str) -> dict[str, Any]:
    store = _store(request)
    try:
        prediction = store.get_prediction(prediction_id)
    except KeyError as exc:
        raise HTTPException(404, "prediction not found") from exc
    return {"prediction": prediction.to_dict(), "side_effects": False}


@router.post("/observations")
async def add_observation(
    payload: ObservationRequest, request: Request
) -> dict[str, Any]:
    """Append a typed observation; this endpoint has no action capability."""

    store = _store(request)
    world = LivingWorldModel(store)
    observation = world.observe(
        source_id=payload.source_id,
        source_type=payload.source_type,
        entity_kind=payload.entity_kind,
        entity_name=payload.entity_name,
        predicate=payload.predicate,
        value=payload.value,
        information_kind=payload.information_kind,
        claim_class=payload.claim_class,
        confidence=payload.confidence,
        observed_at=payload.observed_at,
        recorded_at=payload.recorded_at,
        evidence_text=payload.evidence_text,
        provenance=payload.provenance,
        taint_labels=tuple(payload.taint_labels),
        correction=payload.correction,
    )
    return {"observation": observation.to_dict(), "side_effects": False}


def configure_phase5(app: Any) -> None:
    """Opt in to the Phase 5 database; keep production behavior unchanged by default."""

    if os.environ.get("OPHANIM_PHASE_5_ENABLED", "0") != "1":
        app.state.phase5_store = None
        app.state.phase5_world = None
        app.state.phase5_memory = None
        return
    db_path = os.environ.get("OPHANIM_PHASE5_DB", "").strip()
    if not db_path:
        db_path = str(get_config_dir() / "phase5-world-model.db")
    store = WorldModelStore(Path(db_path))
    app.state.phase5_store = store
    app.state.phase5_world = LivingWorldModel(store)
    app.state.phase5_memory = LayeredMemory(store)

    @app.on_event("shutdown")
    async def _shutdown_phase5() -> None:
        stored = getattr(app.state, "phase5_store", None)
        if stored is not None:
            stored.close()
            app.state.phase5_store = None
            app.state.phase5_world = None
            app.state.phase5_memory = None


__all__ = ["configure_phase5", "router"]
