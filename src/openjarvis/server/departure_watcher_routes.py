"""API and runtime wiring for durable one-time departure watchers."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openjarvis.departure import DepartureWatcherService

router = APIRouter(prefix="/v1/departure-watchers", tags=["departure-watchers"])


class CreateDepartureWatcherRequest(BaseModel):
    conversation_id: str = Field(default="", max_length=256)
    duration_seconds: int = Field(default=900, ge=1, le=900)
    mode: str = Field(default="simulation", pattern="^(simulation|live)$")
    live_approved: bool = False


class LiveApprovalRequest(BaseModel):
    authority: str = Field(default="Marc", min_length=1, max_length=64)


class CancelWatcherRequest(BaseModel):
    reason: str = Field(default="Marc canceled departure watcher", max_length=500)


def _service(request: Request) -> DepartureWatcherService:
    service = getattr(request.app.state, "departure_watcher_service", None)
    if service is None:
        raise HTTPException(
            503,
            "Departure watcher is unavailable because the durable context database or Home Assistant bridge is not configured.",
        )
    return service


def _conversation_id(payload: CreateDepartureWatcherRequest, request: Request) -> str:
    return (
        payload.conversation_id.strip()
        or request.headers.get("X-Conversation-ID", "").strip()
        or request.headers.get("X-Thread-ID", "").strip()
        or "server-request"
    )


@router.post("")
async def create_departure_watcher(
    payload: CreateDepartureWatcherRequest, request: Request
) -> dict[str, Any]:
    service = _service(request)
    if payload.mode == "live" and not payload.live_approved:
        raise HTTPException(403, "Live mode requires an explicit approval; use simulation mode first.")
    try:
        return service.create_watcher(
            conversation_id=_conversation_id(payload, request),
            duration_seconds=payload.duration_seconds,
            mode=payload.mode,
            live_approved=payload.live_approved,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("")
async def list_departure_watchers(request: Request) -> list[dict[str, Any]]:
    """Return the durable reports the chat/UI needs after a watcher fires."""
    service = _service(request)
    return [
        service.inspect(record["watcher_id"])
        for record in service.store.list_departure_watchers()
    ]


@router.get("/{watcher_id}")
async def inspect_departure_watcher(watcher_id: str, request: Request) -> dict[str, Any]:
    try:
        return _service(request).inspect(watcher_id)
    except KeyError:
        raise HTTPException(404, "Departure watcher not found") from None


@router.post("/{watcher_id}/approve-live")
async def approve_live_departure_watcher(
    watcher_id: str, payload: LiveApprovalRequest, request: Request
) -> dict[str, Any]:
    try:
        return _service(request).approve_live(watcher_id, authority=payload.authority)
    except KeyError:
        raise HTTPException(404, "Departure watcher not found") from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{watcher_id}/cancel")
async def cancel_departure_watcher(
    watcher_id: str, payload: CancelWatcherRequest, request: Request
) -> dict[str, Any]:
    try:
        return _service(request).cancel(watcher_id, payload.reason)
    except KeyError:
        raise HTTPException(404, "Departure watcher not found") from None


def configure_departure_watchers(app: Any) -> None:
    """Attach the watcher to the shared context bus and reload active rows."""
    app.state.departure_watcher_service = None
    store = getattr(app.state, "context_store", None)
    if store is None:
        return
    bridge = getattr(app.state, "home_assistant_bridge", None)
    guardian = getattr(app.state, "guardian", None)
    monitor_available = bool(
        bridge is not None
        and bool(getattr(bridge, "is_configured", False))
    )
    state_reader = None
    if monitor_available:
        try:
            from openjarvis.departure import LiveHomeAssistantAdapter

            state_reader = LiveHomeAssistantAdapter(
                url=(
                    os.environ.get("HA_URL", "").strip()
                    or os.environ.get("HOME_ASSISTANT_URL", "").strip()
                    or None
                ),
                token=(
                    os.environ.get("HA_TOKEN", "").strip()
                    or os.environ.get("HOME_ASSISTANT_TOKEN", "").strip()
                    or None
                ),
            )
        except (RuntimeError, ValueError):
            state_reader = None
    service = DepartureWatcherService(
        store,
        guardian=guardian,
        home_assistant_bridge=bridge,
        state_reader=state_reader,
        bus=getattr(app.state, "bus", None),
        monitor_available=monitor_available,
    )
    app.state.departure_watcher_service = service
    service.start()

    @app.on_event("shutdown")
    async def _shutdown_departure_watchers() -> None:
        current = getattr(app.state, "departure_watcher_service", None)
        if current is not None:
            current.stop()
        app.state.departure_watcher_service = None


__all__ = ["configure_departure_watchers", "router"]
