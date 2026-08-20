"""Local Guardian control and causal-timeline API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openjarvis.cognition import ActionLedger, ActionProposal
from openjarvis.core.paths import get_config_dir
from openjarvis.guardian import (
    ActionRegistry,
    GuardianKernel,
    register_home_assistant_actions,
)
from openjarvis.tools.home_assistant import HomeAssistantTool

router = APIRouter(prefix="/v1/guardian", tags=["guardian"])


class GrantRequest(BaseModel):
    grant_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    capability: str = Field(min_length=1, max_length=256)
    scope: dict[str, Any]
    expires_in_seconds: int | None = Field(default=None, ge=1, le=86_400)


class ActionRequest(BaseModel):
    action_type: str = Field(min_length=1, max_length=256)
    parameters: dict[str, Any]
    idempotency_key: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    description: str = ""


class EmergencyStopRequest(BaseModel):
    reason: str = "Marc requested emergency stop"


def _guardian(request: Request) -> GuardianKernel:
    guardian = getattr(request.app.state, "guardian", None)
    if guardian is None:
        raise HTTPException(503, "Guardian is not configured")
    return guardian


@router.post("/grants")
async def create_grant(payload: GrantRequest, request: Request) -> dict[str, str]:
    """Persist Marc's narrow explicit grant; no action is run here."""
    try:
        _guardian(request).grant(**payload.model_dump())
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"grant_id": payload.grant_id, "status": "active"}


@router.post("/actions")
async def execute_action(payload: ActionRequest, request: Request) -> dict[str, Any]:
    """Authorize and run one registered action against an existing grant."""
    proposal = ActionProposal(
        action_type=payload.action_type,
        description=payload.description,
        parameters=payload.parameters,
        idempotency_key=payload.idempotency_key,
        provenance={"component": "guardian-api"},
    )
    guardian = _guardian(request)
    decision = guardian.authorize(
        proposal, session_id=payload.session_id, authority="Marc"
    )
    if not decision.allowed:
        return {
            "action_id": decision.authorization.action_id,
            "allowed": False,
            "reason": decision.reason,
            "state": guardian.ledger.state(decision.authorization.action_id).value,
        }
    result = guardian.execute(proposal.id, decision.authorization)
    return {
        "action_id": proposal.id,
        "allowed": True,
        "state": result.state.value,
        "verification": result.verification.to_dict() if result.verification else None,
        "exception_summary": result.exception_summary,
    }


@router.get("/actions/{action_id}")
async def action_timeline(action_id: str, request: Request) -> dict[str, Any]:
    try:
        return _guardian(request).timeline(action_id)
    except KeyError:
        raise HTTPException(404, "Guardian action not found") from None


@router.post("/emergency-stop")
async def emergency_stop(
    payload: EmergencyStopRequest, request: Request
) -> dict[str, str]:
    _guardian(request).emergency_stop(reason=payload.reason)
    return {"status": "active"}


@router.post("/emergency-stop/clear")
async def clear_emergency_stop(request: Request) -> dict[str, str]:
    _guardian(request).clear_emergency_stop(authority="Marc")
    return {"status": "cleared"}


def configure_guardian(app: Any) -> None:
    """Create the shared local Guardian before optional Codex integration."""
    if getattr(app.state, "guardian", None) is not None:
        return
    db_path = os.environ.get("OPHANIM_GUARDIAN_DB", "").strip()
    if not db_path:
        db_path = str(get_config_dir() / "guardian.db")
    ledger = ActionLedger(Path(db_path))
    registry = ActionRegistry()
    register_home_assistant_actions(registry, HomeAssistantTool())
    app.state.guardian_ledger = ledger
    app.state.guardian = GuardianKernel(ledger, registry)

    @app.on_event("shutdown")
    async def _shutdown_guardian() -> None:
        guardian = getattr(app.state, "guardian", None)
        if guardian is not None:
            guardian.close()
            app.state.guardian = None
        stored_ledger = getattr(app.state, "guardian_ledger", None)
        if stored_ledger is not None:
            stored_ledger.close()
            app.state.guardian_ledger = None
