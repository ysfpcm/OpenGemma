"""Phase 7 Departure Guardian API and live-standby runtime wiring."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openjarvis.core.paths import get_config_dir
from openjarvis.departure import (
    DepartureGuardian,
    DepartureObservations,
    DepartureSetup,
    DepartureStore,
    LiveHomeAssistantAdapter,
    LiveNotificationAdapter,
    Phase7Flags,
    register_departure_adapters,
)

router = APIRouter(prefix="/v1/departure", tags=["departure-guardian"])


class SetupRequest(BaseModel):
    setup: dict[str, Any]


class ObservationRequest(BaseModel):
    observations: dict[str, Any]


class EditRequest(BaseModel):
    remove_step_ids: list[str] = Field(min_length=1)
    edit_id: str
    reason: str
    observations: dict[str, Any]


class ApprovalRequest(BaseModel):
    step_id: str
    observations: dict[str, Any]
    approval_id: str | None = None
    expires_at: str | None = None


class ScheduleRequest(BaseModel):
    grant_ids: dict[str, str] = Field(default_factory=dict)
    approval_ids: dict[str, str] = Field(default_factory=dict)


class RunRequest(BaseModel):
    observations: dict[str, Any]


class CancelRequest(BaseModel):
    reason: str = "Marc canceled Departure"


def _service(request: Request) -> DepartureGuardian:
    service = getattr(request.app.state, "phase7_departure", None)
    if service is None:
        raise HTTPException(503, "Phase 7 Departure Guardian is disabled")
    return service


def _observations(value: dict[str, Any]) -> DepartureObservations:
    try:
        return DepartureObservations(
            departure_id=str(value["departure_id"]),
            now=str(value["now"]),
            calendar_event_id=str(value["calendar_event_id"]),
            calendar_event_start=str(value["calendar_event_start"]),
            calendar_title=str(value.get("calendar_title", "")),
            calendar_status=str(value.get("calendar_status", "confirmed")),
            destination=str(value.get("destination", "")),
            traffic_minutes=(
                int(value["traffic_minutes"])
                if value.get("traffic_minutes") is not None
                else None
            ),
            weather_summary=value.get("weather_summary"),
            weather_precipitation=value.get("weather_precipitation"),
            marc_present=value.get("marc_present"),
            present_people=tuple(str(item) for item in value.get("present_people", [])),
            household_mode=str(value.get("household_mode", "home")),
            workspace=str(value.get("workspace", "local")),
            location=str(value.get("location", "home")),
            actual_departure_detected=bool(
                value.get("actual_departure_detected", False)
            ),
            return_home=bool(value.get("return_home", False)),
            manual_reversal=bool(value.get("manual_reversal", False)),
            conflicting_plan_id=value.get("conflicting_plan_id"),
            source_status=dict(value.get("source_status", {})),
            source_observed_at=dict(value.get("source_observed_at", {})),
            source_confidence=dict(value.get("source_confidence", {})),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", [])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/setups")
async def save_setup(payload: SetupRequest, request: Request) -> dict[str, Any]:
    try:
        setup = DepartureSetup.from_dict(payload.setup)
        saved = _service(request).save_setup(setup)
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"setup": saved.to_dict(), "side_effects": False}


@router.get("/setups/{setup_id}")
async def inspect_setup(setup_id: str, request: Request) -> dict[str, Any]:
    try:
        service = _service(request)
        return {
            "setup": service.setup(setup_id).to_dict(),
            "versions": [
                item.to_dict() for item in service.store.list_setup_versions(setup_id)
            ],
            "side_effects": False,
        }
    except KeyError:
        raise HTTPException(404, "Departure setup not found") from None


@router.post("/setups/{setup_id}/recalculate")
async def recalculate(
    setup_id: str, payload: ObservationRequest, request: Request
) -> dict[str, Any]:
    try:
        result = _service(request).recalculate(
            setup_id, _observations(payload.observations)
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"result": result.to_dict(), "side_effects": False}


@router.get("/status")
async def departure_status(request: Request) -> dict[str, Any]:
    service = getattr(request.app.state, "phase7_departure", None)
    flags = getattr(service, "flags", None)
    return {
        "enabled": service is not None,
        "adapter_mode": getattr(request.app.state, "phase7_adapter_mode", "disabled"),
        "standby": service is not None
        and not bool(getattr(flags, "delayed_execution_enabled", False)),
        "delayed_execution_enabled": bool(
            getattr(flags, "delayed_execution_enabled", False)
        ),
        "side_effects": bool(
            service is not None and getattr(flags, "delayed_execution_enabled", False)
        ),
    }


@router.get("/{departure_id}")
async def inspect_departure(departure_id: str, request: Request) -> dict[str, Any]:
    try:
        return _service(request).inspect(departure_id)
    except KeyError:
        raise HTTPException(404, "Departure not found") from None


@router.post("/{departure_id}/edit")
async def edit_departure(
    departure_id: str, payload: EditRequest, request: Request
) -> dict[str, Any]:
    try:
        plan = _service(request).edit_plan(
            departure_id,
            remove_step_ids=payload.remove_step_ids,
            edit_id=payload.edit_id,
            reason=payload.reason,
            observations=_observations(payload.observations),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"plan": plan.to_dict(), "side_effects": False}


@router.post("/{departure_id}/approve")
async def approve_departure(
    departure_id: str, payload: ApprovalRequest, request: Request
) -> dict[str, Any]:
    try:
        approval = _service(request).approve_step(
            departure_id,
            payload.step_id,
            _observations(payload.observations),
            approval_id=payload.approval_id,
            expires_at=payload.expires_at,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"approval": approval, "side_effects": False}


@router.post("/{departure_id}/schedule")
async def schedule_departure(
    departure_id: str, payload: ScheduleRequest, request: Request
) -> dict[str, Any]:
    try:
        schedules = _service(request).schedule_plan(
            departure_id, grant_ids=payload.grant_ids, approval_ids=payload.approval_ids
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"schedules": schedules, "side_effects": False}


@router.post("/approvals/{approval_id}/revoke")
async def revoke_approval(
    approval_id: str, payload: CancelRequest, request: Request
) -> dict[str, Any]:
    try:
        service = _service(request)
        service.revoke_approval(approval_id, payload.reason)
    except KeyError:
        raise HTTPException(404, "Departure approval not found") from None
    return {"approval_id": approval_id, "status": "revoked", "side_effects": False}


@router.post("/{departure_id}/run")
async def run_departure(
    departure_id: str, payload: RunRequest, request: Request
) -> dict[str, Any]:
    try:
        service = _service(request)
        schedules = service.run_due(departure_id, _observations(payload.observations))
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "schedules": schedules,
        "report": service.completion_report(departure_id),
        "side_effects": bool(
            service.flags.enabled and service.flags.delayed_execution_enabled
        ),
    }


@router.post("/{departure_id}/cancel")
async def cancel_departure(
    departure_id: str, payload: CancelRequest, request: Request
) -> dict[str, Any]:
    try:
        service = _service(request)
        service.cancel(departure_id, payload.reason)
        return service.inspect(departure_id)
    except KeyError:
        raise HTTPException(404, "Departure not found") from None


@router.get("/reports/weekly/{week_start}")
async def weekly_report(week_start: str, request: Request) -> dict[str, Any]:
    return {
        "report": _service(request).weekly_trust_report(week_start),
        "side_effects": False,
    }


def configure_phase7(app: Any) -> None:
    """Enable Phase 7 with real integrations and no effects by default.

    Standby connects live integrations for state/health inspection and typed
    planning; delayed execution remains governed by the feature flag and setup
    policy. There is no fake-adapter fallback in the server path.
    """

    app.state.phase7_departure = None
    app.state.phase7_adapter_mode = "disabled"
    if os.environ.get("OPHANIM_PHASE_7_ENABLED", "0") != "1":
        return
    guardian = getattr(app.state, "guardian", None)
    planning = getattr(app.state, "phase6_controller", None)
    if guardian is None or planning is None:
        return
    ha_token = (
        os.environ.get("HA_TOKEN", "").strip()
        or os.environ.get("HOME_ASSISTANT_TOKEN", "").strip()
    )
    if not ha_token:
        app.state.phase7_adapter_mode = "live-not-configured"
        return
    try:
        home = LiveHomeAssistantAdapter(token=ha_token)
    except (RuntimeError, ValueError):
        app.state.phase7_adapter_mode = "live-not-configured"
        return
    notifications = LiveNotificationAdapter(getattr(app.state, "channel_bridge", None))
    register_departure_adapters(guardian.registry, home, notifications)
    db_path = os.environ.get("OPHANIM_PHASE7_DB", "").strip()
    if not db_path:
        db_path = str(get_config_dir() / "phase7-departure.db")
    store = DepartureStore(Path(db_path))
    app.state.phase7_departure = DepartureGuardian(
        guardian,
        planning,
        store,
        flags=Phase7Flags.from_environment(),
    )
    app.state.phase7_adapter_mode = "live-standby"
    app.state.phase7_home_assistant = home
    app.state.phase7_notifications = notifications

    @app.on_event("shutdown")
    async def _shutdown_phase7() -> None:
        service = getattr(app.state, "phase7_departure", None)
        if service is not None:
            service.close()
        app.state.phase7_departure = None


__all__ = ["configure_phase7", "router"]
