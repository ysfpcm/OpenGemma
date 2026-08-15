"""Read-only Operations API for observed Codex missions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.codex_observer import CodexMissionStore, CodexObserverSupervisor
from openjarvis.core.paths import get_config_dir

router = APIRouter(prefix="/v1/codex", tags=["codex-observer"])


class MissionStartRequest(BaseModel):
    objective: str
    workspace: str


def _supervisor(request: Request) -> CodexObserverSupervisor:
    supervisor = getattr(request.app.state, "codex_observer", None)
    if supervisor is None:
        raise HTTPException(503, "Codex observer is not configured")
    return supervisor


@router.get("/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    supervisor = _supervisor(request)
    return supervisor.capabilities


@router.get("/missions")
async def list_missions(request: Request) -> dict[str, Any]:
    missions = _supervisor(request).store.list()
    return {"missions": missions, "count": len(missions), "mode": "read-only"}


@router.get("/missions/{mission_id}")
async def get_mission(mission_id: str, request: Request) -> dict[str, Any]:
    try:
        return _supervisor(request).store.get(mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None


@router.post("/missions/{mission_id}/resume")
async def resume_mission(mission_id: str, request: Request) -> dict[str, Any]:
    try:
        _supervisor(request).resume_observation(mission_id)
        return _supervisor(request).store.get(mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None


@router.get("/threads")
async def list_threads(request: Request) -> dict[str, Any]:
    return _supervisor(request).list_threads()


@router.get("/threads/{thread_id}")
async def read_thread(thread_id: str, request: Request) -> dict[str, Any]:
    return _supervisor(request).read_thread(thread_id)


@router.post("/missions")
async def start_mission(
    payload: MissionStartRequest, request: Request
) -> dict[str, Any]:
    try:
        return _supervisor(request).start_mission(payload.objective, payload.workspace)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


def configure_codex_observer(app: Any) -> None:
    db_path = os.environ.get("OPHANIM_CODEX_OBSERVER_DB", "").strip()
    if not db_path:
        db_path = str(get_config_dir() / "codex-observer.db")
    roots_value = os.environ.get("OPHANIM_CODEX_OBSERVER_ROOTS", "").strip()
    roots = [item for item in roots_value.split(os.pathsep) if item]
    app.state.codex_observer = None
    if not roots:
        return
    try:
        store = CodexMissionStore(db_path)
        app.state.codex_observer = CodexObserverSupervisor(
            store, roots=[str(Path(item)) for item in roots], bus=app.state.bus
        )

        @app.on_event("shutdown")
        async def _shutdown_codex_observer() -> None:
            observer = getattr(app.state, "codex_observer", None)
            if observer:
                observer.stop()
                observer.store.close()
    except Exception:
        app.state.codex_observer = None
