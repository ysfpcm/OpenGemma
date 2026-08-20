"""Read-only Operations API for observed Codex missions."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.codex_observer import CodexMissionStore, CodexObserverSupervisor
from openjarvis.core.paths import get_config_dir

router = APIRouter(prefix="/v1/codex", tags=["codex-observer"])


class MissionStartRequest(BaseModel):
    objective: str
    workspace: str
    mode: Literal["read-only", "workspace-write"] = "read-only"
    budgets: dict[str, int | float] = {}


class SteerRequest(BaseModel):
    text: str


class ResumeRequest(BaseModel):
    instruction: str


class DecisionRequest(BaseModel):
    decision: str | None = None
    answers: dict[str, Any] | None = None
    content: dict[str, Any] | None = None


class BudgetRequest(BaseModel):
    budgets: dict[str, int | float]


def _supervisor(request: Request) -> CodexObserverSupervisor:
    supervisor = getattr(request.app.state, "codex_observer", None)
    if supervisor is None:
        raise HTTPException(503, "Codex observer is not configured")
    return supervisor


@router.get("/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    supervisor = _supervisor(request)
    return {
        **supervisor.capabilities,
        "workspace_roots": [str(root) for root in supervisor.roots],
    }


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


@router.get("/missions/{mission_id}/decisions")
async def list_decisions(mission_id: str, request: Request) -> dict[str, Any]:
    try:
        decisions = _supervisor(request).store.list_decisions(mission_id)
        return {"decisions": decisions}
    except KeyError:
        raise HTTPException(404, "Mission not found") from None


@router.post("/missions/{mission_id}/decisions/{decision_id}")
async def resolve_decision(
    mission_id: str, decision_id: str, payload: DecisionRequest, request: Request
) -> dict[str, Any]:
    try:
        decision = _supervisor(request).store.get_decision(decision_id)
        if decision["mission_id"] != mission_id:
            raise HTTPException(404, "Decision not found")
        body = payload.model_dump(exclude_none=True)
        return _supervisor(request).resolve_decision(decision_id, body)
    except KeyError:
        raise HTTPException(404, "Decision not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/missions/{mission_id}/checkpoint")
async def request_checkpoint(mission_id: str, request: Request) -> dict[str, Any]:
    try:
        _supervisor(request).request_checkpoint(mission_id)
        return _supervisor(request).store.get(mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/missions/{mission_id}/budgets")
async def update_budgets(
    mission_id: str, payload: BudgetRequest, request: Request
) -> dict[str, Any]:
    # Budgets are immutable during a mission.  This endpoint is intentionally
    # a read-only check surface so Marc cannot use it to widen authority while
    # a turn is active.
    try:
        mission = _supervisor(request).store.get(mission_id, include_events=False)
        if mission["status"] not in {"starting", "paused"}:
            raise HTTPException(409, "mission budgets cannot change after start")
        _supervisor(request)._budgets(payload.budgets)
        _supervisor(request).store.update(mission_id, budgets_json=payload.budgets)
        return _supervisor(request).store.get(mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None


@router.get("/missions/{mission_id}/budget-state")
async def budget_state(mission_id: str, request: Request) -> dict[str, Any]:
    try:
        return _supervisor(request).check_budgets(mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None


@router.get("/missions/{mission_id}/compare/{other_mission_id}")
async def compare_forks(
    mission_id: str, other_mission_id: str, request: Request
) -> dict[str, Any]:
    try:
        return _supervisor(request).compare_forks(mission_id, other_mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/missions/{mission_id}/select-fork/{fork_mission_id}")
async def select_fork(
    mission_id: str, fork_mission_id: str, request: Request
) -> dict[str, Any]:
    try:
        return _supervisor(request).select_fork(mission_id, fork_mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/missions/{mission_id}/resume")
async def resume_observation(mission_id: str, request: Request) -> dict[str, Any]:
    try:
        _supervisor(request).resume_observation(mission_id)
        return _supervisor(request).store.get(mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None


@router.post("/missions/{mission_id}/steer")
async def steer_mission(
    mission_id: str, payload: SteerRequest, request: Request
) -> dict[str, Any]:
    try:
        _supervisor(request).steer_mission(mission_id, payload.text)
        return _supervisor(request).store.get(mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/missions/{mission_id}/interrupt")
async def interrupt_mission(mission_id: str, request: Request) -> dict[str, Any]:
    try:
        _supervisor(request).interrupt_mission(mission_id)
        return _supervisor(request).store.get(mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/missions/{mission_id}/resume-turn")
async def resume_mission(
    mission_id: str, payload: ResumeRequest, request: Request
) -> dict[str, Any]:
    try:
        return _supervisor(request).resume_mission(mission_id, payload.instruction)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/missions/{mission_id}/fork")
async def fork_mission(mission_id: str, request: Request) -> dict[str, Any]:
    try:
        return _supervisor(request).fork_mission(mission_id)
    except KeyError:
        raise HTTPException(404, "Mission not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


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
        return await asyncio.to_thread(
            _supervisor(request).start_mission,
            payload.objective,
            payload.workspace,
            mode=payload.mode,
            budgets=payload.budgets,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


def configure_codex_observer(app: Any) -> None:
    db_path = os.environ.get("OPHANIM_CODEX_OBSERVER_DB", "").strip()
    if not db_path:
        db_path = str(get_config_dir() / "codex-observer.db")
    roots_value = os.environ.get("OPHANIM_CODEX_OBSERVER_ROOTS", "").strip()
    roots = [item for item in roots_value.split(os.pathsep) if item]
    if not roots:
        server_config = getattr(getattr(app.state, "config", None), "server", None)
        roots = list(getattr(server_config, "codex_observer_roots", []) or [])
    app.state.codex_observer = None
    app.state.codex_guardian_bridge = None
    if not roots:
        return
    try:
        from openjarvis.guardian import CodexGuardianBridge

        guardian = getattr(app.state, "guardian", None)
        if guardian is None:
            raise RuntimeError("Guardian must be configured before Codex observer")
        app.state.codex_guardian_bridge = CodexGuardianBridge(app.state.guardian)
        store = CodexMissionStore(db_path)
        app.state.codex_observer = CodexObserverSupervisor(
            store,
            roots=[str(Path(item)) for item in roots],
            bus=app.state.bus,
            guardian_bridge=app.state.codex_guardian_bridge,
        )

        @app.on_event("shutdown")
        async def _shutdown_codex_observer() -> None:
            observer = getattr(app.state, "codex_observer", None)
            if observer:
                observer.stop()
                observer.store.close()
    except Exception:
        app.state.codex_observer = None
