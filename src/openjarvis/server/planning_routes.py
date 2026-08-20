"""Inspection and review API for the opt-in Phase 6 planning slice."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openjarvis.core.paths import get_config_dir
from openjarvis.planning import (
    AutonomyLevel,
    ContextualGrant,
    Phase6Controller,
    PlanningContext,
    PlanningStore,
    StructuredPlan,
)

router = APIRouter(prefix="/v1/planning", tags=["planning"])


class PlanCreateRequest(BaseModel):
    plan: dict[str, Any]
    context: dict[str, Any]


class PlanEditRequest(BaseModel):
    remove_step_ids: list[str] = Field(min_length=1)
    edit_id: str
    reason: str
    context: dict[str, Any]


class PlanCancelRequest(BaseModel):
    reason: str = "Marc canceled plan"


class GrantCreateRequest(BaseModel):
    grant: dict[str, Any]


class AuthorizationRequest(BaseModel):
    plan_version: int = Field(ge=1)
    step_id: str
    context: dict[str, Any]
    grant_id: str | None = None
    marc_approved: bool = False


class PolicyRequest(BaseModel):
    level: int = Field(ge=0, le=4)


def _controller(request: Request) -> Phase6Controller:
    controller = getattr(request.app.state, "phase6_controller", None)
    if controller is None:
        raise HTTPException(503, "Phase 6 structured planning is disabled")
    return controller


@router.post("/plans")
async def create_plan(payload: PlanCreateRequest, request: Request) -> dict[str, Any]:
    controller = _controller(request)
    try:
        plan = StructuredPlan.from_dict(payload.plan)
        context = PlanningContext.from_dict(payload.context)
        controller.create_plan(plan, context)
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"plan": plan.to_dict(), "side_effects": False}


@router.get("/plans/{plan_id}")
async def inspect_plan(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        return _controller(request).inspect_plan(plan_id)
    except KeyError:
        raise HTTPException(404, "plan not found") from None


@router.get("/plans/{plan_id}/versions")
async def list_plan_versions(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        controller = _controller(request)
        return {
            "versions": [
                item.to_dict() for item in controller.store.list_versions(plan_id)
            ],
            "edits": controller.store.edit_history(plan_id),
            "side_effects": False,
        }
    except KeyError:
        raise HTTPException(404, "plan not found") from None


@router.post("/plans/{plan_id}/edits")
async def edit_plan(
    plan_id: str, payload: PlanEditRequest, request: Request
) -> dict[str, Any]:
    controller = _controller(request)
    try:
        plan = controller.edit_remove_steps(
            plan_id,
            remove_step_ids=payload.remove_step_ids,
            edit_id=payload.edit_id,
            reason=payload.reason,
            context=PlanningContext.from_dict(payload.context),
        )
    except KeyError:
        raise HTTPException(404, "plan not found") from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"plan": plan.to_dict(), "side_effects": False}


@router.post("/plans/{plan_id}/revalidate")
async def revalidate_plan(
    plan_id: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    controller = _controller(request)
    try:
        report = controller.planner.revalidate(
            plan_id, PlanningContext.from_dict(payload)
        )
    except KeyError:
        raise HTTPException(404, "plan not found") from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"validation": report.to_dict(), "side_effects": False}


@router.post("/plans/{plan_id}/cancel")
async def cancel_plan(
    plan_id: str, payload: PlanCancelRequest, request: Request
) -> dict[str, Any]:
    try:
        controller = _controller(request)
        controller.cancel_plan(plan_id, payload.reason)
        return controller.inspect_plan(plan_id)
    except KeyError:
        raise HTTPException(404, "plan not found") from None


@router.post("/grants")
async def create_grant(payload: GrantCreateRequest, request: Request) -> dict[str, Any]:
    controller = _controller(request)
    try:
        grant = ContextualGrant.from_dict(payload.grant)
        controller.grant(grant)
    except KeyError:
        raise HTTPException(404, "grant plan or step not found") from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"grant": grant.to_dict(), "side_effects": False}


@router.get("/grants")
async def list_grants(request: Request) -> dict[str, Any]:
    controller = _controller(request)
    return {
        "grants": [item.to_dict() for item in controller.store.list_grants()],
        "side_effects": False,
    }


@router.post("/grants/{grant_id}/revoke")
async def revoke_grant(grant_id: str, request: Request) -> dict[str, Any]:
    try:
        _controller(request).revoke_grant(grant_id)
    except KeyError:
        raise HTTPException(404, "grant not found") from None
    return {"grant_id": grant_id, "status": "revoked", "side_effects": False}


@router.post("/plans/{plan_id}/authorize")
async def authorize_step(
    plan_id: str, payload: AuthorizationRequest, request: Request
) -> dict[str, Any]:
    controller = _controller(request)
    try:
        decision = controller.authorize(
            plan_id,
            payload.plan_version,
            payload.step_id,
            PlanningContext.from_dict(payload.context),
            grant_id=payload.grant_id,
            marc_approved=payload.marc_approved,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"decision": decision.to_dict(), "side_effects": False}


@router.post("/policies/{action_type}")
async def set_policy(
    action_type: str, payload: PolicyRequest, request: Request
) -> dict[str, Any]:
    try:
        level = AutonomyLevel(payload.level)
        _controller(request).set_autonomy_level(action_type, level)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"action_type": action_type, "level": level.value, "side_effects": False}


def configure_phase6(app: Any) -> None:
    """Enable Phase 6 only for an explicitly configured local process."""

    if os.environ.get("OPHANIM_PHASE_6_ENABLED", "0") != "1":
        app.state.phase6_store = None
        app.state.phase6_controller = None
        return
    guardian = getattr(app.state, "guardian", None)
    if guardian is None:
        app.state.phase6_store = None
        app.state.phase6_controller = None
        return
    db_path = os.environ.get("OPHANIM_PHASE6_DB", "").strip()
    if not db_path:
        db_path = str(get_config_dir() / "phase6-planning.db")
    store = PlanningStore(Path(db_path))
    app.state.phase6_store = store
    app.state.phase6_controller = Phase6Controller(guardian, store)

    @app.on_event("shutdown")
    async def _shutdown_phase6() -> None:
        controller = getattr(app.state, "phase6_controller", None)
        if controller is not None:
            controller.close()
        app.state.phase6_store = None
        app.state.phase6_controller = None


__all__ = ["configure_phase6", "router"]
