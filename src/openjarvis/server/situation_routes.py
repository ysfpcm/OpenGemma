"""Read-only Phase 4 event ingestion and shadow-situation endpoints."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openjarvis.core.paths import get_config_dir
from openjarvis.situations import (
    DurableEvent,
    DurableEventJournal,
    ShadowSituationDetector,
)

router = APIRouter(prefix="/v1/situations", tags=["situations"])


class EventIngestRequest(BaseModel):
    event_type: str
    source: str
    observed_at: str
    payload: dict[str, Any]
    source_event_id: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    sensitivity_labels: list[str] = Field(default_factory=list)
    taint_labels: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] | None = None


def _detector(request: Request) -> ShadowSituationDetector:
    detector = getattr(request.app.state, "phase4_detector", None)
    if detector is None:
        raise HTTPException(503, "Phase 4 shadow situations are disabled")
    return detector


@router.post("/events")
async def ingest_event(payload: EventIngestRequest, request: Request) -> dict[str, Any]:
    """Persist first, then run deterministic shadow detection.

    This endpoint has no Guardian or executor reference. A successful response
    means only that an observation and/or shadow record was persisted.
    """

    detector = _detector(request)
    try:
        event = DurableEvent(**payload.model_dump())
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = detector.journal.append(event)
    reports = detector.process_available()
    return {
        "inserted": result.inserted,
        "event": result.event.to_dict(),
        "reports": [report.to_dict() for report in reports],
        "situations": [item.to_dict() for item in detector.journal.situations()],
        "side_effects": False,
    }


@router.post("/replay")
async def replay_situations(request: Request) -> dict[str, Any]:
    detector = _detector(request)
    report = detector.replay()
    return {
        "report": report.to_dict(),
        "situations": [item.to_dict() for item in detector.journal.situations()],
        "side_effects": False,
    }


@router.get("")
async def list_situations(request: Request) -> dict[str, Any]:
    detector = _detector(request)
    return {
        "mode": "shadow",
        "situations": [item.to_dict() for item in detector.journal.situations()],
        "evaluations": detector.journal.evaluations(),
        "dead_letters": detector.journal.dead_letters("shadow-situations"),
        "side_effects": False,
    }


def configure_phase4(app: Any) -> None:
    """Enable Phase 4 only when explicitly opted in for this process."""

    if os.environ.get("OPHANIM_PHASE_4_ENABLED", "0") != "1":
        app.state.phase4_journal = None
        app.state.phase4_detector = None
        return
    db_path = os.environ.get("OPHANIM_PHASE4_DB", "").strip()
    if not db_path:
        db_path = str(get_config_dir() / "phase4-situations.db")
    journal = DurableEventJournal(Path(db_path))
    app.state.phase4_journal = journal
    app.state.phase4_detector = ShadowSituationDetector(journal)

    @app.on_event("shutdown")
    async def _shutdown_phase4() -> None:
        stored = getattr(app.state, "phase4_journal", None)
        if stored is not None:
            stored.close()
            app.state.phase4_journal = None
            app.state.phase4_detector = None


__all__ = ["configure_phase4", "router"]
