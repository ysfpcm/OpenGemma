"""WebSocket bridge: EventBus → connected WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any

from openjarvis.context.home_assistant import redact_secrets
from openjarvis.core.events import Event, EventBus, EventType

try:
    from fastapi import APIRouter, WebSocket, WebSocketDisconnect
except ImportError:  # pragma: no cover
    pass  # FastAPI is optional; create_ws_router will fail at call time

logger = logging.getLogger(__name__)

# Agent-related event types to forward
_AGENT_EVENTS = {
    EventType.AGENT_TICK_START,
    EventType.AGENT_TICK_END,
    EventType.AGENT_TICK_ERROR,
    EventType.AGENT_BUDGET_EXCEEDED,
    EventType.AGENT_STALL_DETECTED,
    EventType.AGENT_MESSAGE_RECEIVED,
    EventType.AGENT_CHECKPOINT_SAVED,
    EventType.CONTEXT_UPDATED,
    EventType.TOOL_CALL_START,
    EventType.TOOL_CALL_END,
    EventType.INFERENCE_START,
    EventType.INFERENCE_END,
}

# Events useful to a human operator. High-volume debug/telemetry events are
# intentionally excluded so the browser shows meaningful activity rather than
# an unbounded firehose.
_CONSOLE_EVENTS = {
    EventType.INFERENCE_START,
    EventType.INFERENCE_END,
    EventType.TOOL_CALL_START,
    EventType.TOOL_CALL_END,
    EventType.TOOL_TIMEOUT,
    EventType.MEMORY_STORE,
    EventType.MEMORY_RETRIEVE,
    EventType.CONTEXT_ASSEMBLED,
    EventType.TRACE_STEP,
    EventType.TRACE_COMPLETE,
    EventType.CHANNEL_MESSAGE_RECEIVED,
    EventType.CHANNEL_MESSAGE_SENT,
    EventType.SECURITY_SCAN,
    EventType.SECURITY_ALERT,
    EventType.SECURITY_BLOCK,
    EventType.CAPABILITY_DENIED,
    EventType.TAINT_VIOLATION,
    EventType.SCHEDULER_TASK_START,
    EventType.SCHEDULER_TASK_END,
    EventType.WORKFLOW_START,
    EventType.WORKFLOW_NODE_START,
    EventType.WORKFLOW_NODE_END,
    EventType.WORKFLOW_END,
    EventType.SKILL_EXECUTE_START,
    EventType.SKILL_EXECUTE_END,
    EventType.SESSION_START,
    EventType.SESSION_END,
    EventType.AGENT_TICK_START,
    EventType.AGENT_TICK_END,
    EventType.AGENT_TICK_ERROR,
    EventType.AGENT_BUDGET_EXCEEDED,
    EventType.AGENT_STALL_DETECTED,
    EventType.AGENT_MESSAGE_RECEIVED,
    EventType.AGENT_CHECKPOINT_SAVED,
    EventType.A2A_TASK_RECEIVED,
    EventType.A2A_TASK_COMPLETED,
    EventType.CONTEXT_UPDATED,
    EventType.HOME_ASSISTANT_STATUS,
    EventType.CAMERA_MOTION,
    EventType.MOTION_HEARTBEAT,
    EventType.MOTION_CLEARED,
    EventType.PERSON_DETECTED,
    EventType.SOUND_DETECTED,
    EventType.DOORBELL_CHIME,
    EventType.CAMERA_STATUS,
    EventType.SNAPSHOT_RECEIVED,
    EventType.SNAPSHOT_FAILED,
}

_CONSOLE_CATEGORIES = {
    "inference": "inference",
    "tool_call": "tools",
    "tool_timeout": "tools",
    "memory": "context",
    "context_assembled": "context",
    "context_updated": "context",
    "trace": "traces",
    "channel": "channels",
    "security": "security",
    "capability_denied": "security",
    "taint_violation": "security",
    "scheduler": "agents",
    "workflow": "agents",
    "skill_execute": "agents",
    "session": "system",
    "agent": "agents",
    "a2a": "agents",
    "home_assistant": "context",
    "motion": "context",
    "camera": "cameras",
    "snapshot": "cameras",
}

_SAFE_CONSOLE_KEYS = {
    "agent",
    "agent_id",
    "tool",
    "model",
    "requested_model",
    "routing_reason",
    "engine",
    "provider",
    "channel",
    "source",
    "source_key",
    "entity_name",
    "entity_type",
    "event_type",
    "area",
    "status",
    "success",
    "duration_ms",
    "latency_ms",
    "latency",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "estimated_prompt_tokens",
    "message_count",
    "original_message_count",
    "memory_messages_added",
    "knowledge_messages_added",
    "live_context_messages_added",
    "result_count",
    "top_k",
    "trace_id",
    "workflow",
    "node",
    "skill",
    "error_type",
    "reason_code",
    "camera",
    "camera_event",
    "motion_event",
    "active",
    "recent_activity",
    "raw_motion_state",
    "snapshot_available",
    "snapshot_id",
    "snapshot_ref",
    "snapshot_status",
    "content_type",
    "size_bytes",
    "context_event_id",
    "changed_state_count",
    "updated_state_count",
}


def _console_category(event_name: str) -> str:
    for prefix, category in _CONSOLE_CATEGORIES.items():
        if event_name.startswith(prefix):
            return category
    return "system"


def _console_level(event_name: str, data: dict[str, Any]) -> str:
    if event_name.endswith("error") or event_name in {
        "security_block",
        "capability_denied",
        "taint_violation",
        "snapshot_failed",
    }:
        return "error"
    if event_name in {"security_alert", "tool_timeout", "agent_stall_detected", "agent_budget_exceeded"}:
        return "warn"
    if event_name in {"camera_status", "home_assistant_status"} and data.get("status") in {"offline", "degraded"}:
        return "warn"
    if data.get("success") is False:
        return "error"
    return "info"


def _console_summary(event_name: str, data: dict[str, Any]) -> str:
    label = event_name.replace("_", " ")
    if event_name.startswith("tool_call") or event_name == "tool_timeout":
        return f"{label} · {data.get('tool', 'tool')}"
    if event_name.startswith("inference"):
        return f"{label} · {data.get('model') or data.get('engine') or 'model'}"
    if event_name.startswith("memory"):
        count = data.get("result_count") or data.get("memory_messages_added")
        return f"{label} · {count} item(s)" if count is not None else label
    if event_name == "context_assembled":
        tokens = data.get("estimated_prompt_tokens", "?")
        return f"context assembled · ~{tokens} prompt tokens"
    if event_name == "context_updated":
        display_name = redact_secrets(data.get("entity_name") or "context")
        return f"context updated · {display_name}"
    if event_name.startswith("channel"):
        return f"{label} · {data.get('channel') or data.get('source') or 'channel'}"
    if (
        event_name.startswith("camera")
        or event_name.startswith("person_")
        or event_name.startswith("sound_")
        or event_name.startswith("doorbell")
    ):
        display_name = redact_secrets(data.get("entity_name") or "camera")
        return f"{label} · {display_name}"
    if event_name.startswith("snapshot"):
        display_name = redact_secrets(data.get("entity_name") or "camera")
        return f"{label} · {display_name}"
    if event_name == "home_assistant_status":
        return f"home assistant · {data.get('status', 'unknown')}"
    if event_name.startswith("agent") or event_name.startswith("workflow"):
        return f"{label} · {data.get('agent_id') or data.get('workflow') or 'runtime'}"
    return label


def _safe_console_payload(event: Event) -> dict[str, Any]:
    """Reduce an EventBus event to operator-safe, non-content metadata."""
    event_name = event.event_type.value
    raw = event.data or {}
    safe_data = {
        key: value
        for key, value in raw.items()
        if key in _SAFE_CONSOLE_KEYS
        and isinstance(value, (str, int, float, bool))
    }
    return {
        "type": event_name,
        "timestamp": event.timestamp,
        "category": _console_category(event_name),
        "level": _console_level(event_name, raw),
        "summary": _console_summary(event_name, raw),
        "data": redact_secrets(safe_data),
    }


class ConsoleEventHistory:
    """Thread-safe, bounded history of sanitized console events."""

    def __init__(self, max_events: int = 2_000) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._next_id = 1

    def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Add a payload and return it with a stable replay id."""
        with self._lock:
            stored = {**payload, "id": self._next_id}
            self._next_id += 1
            self._events.append(stored)
            return stored

    def snapshot(self, limit: int = 2_000) -> list[dict[str, Any]]:
        """Return the newest *limit* events in chronological order."""
        bounded_limit = max(0, min(limit, self._events.maxlen or 2_000))
        with self._lock:
            if bounded_limit == 0:
                return []
            return list(self._events)[-bounded_limit:]


def create_ws_router(
    event_bus: EventBus,
    *,
    history: ConsoleEventHistory | None = None,
) -> Any:
    """Create a FastAPI router with a WebSocket endpoint for agent events."""
    router = APIRouter()
    console_history = history or ConsoleEventHistory()
    # Each connected client gets a queue + loop ref for thread-safe event delivery
    clients: dict[WebSocket, tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = {}
    console_clients: dict[WebSocket, tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = {}

    def _on_event(event: Event) -> None:
        """Forward event to all connected WebSocket client queues (thread-safe)."""
        payload = {
            "type": event.event_type.value,
            "timestamp": event.timestamp,
            "data": event.data or {},
        }
        for ws, (queue, loop) in list(clients.items()):
            agent_filter = getattr(ws, "_agent_filter", None)
            # Tick events carry "agent_id"; tool-call events carry "agent".
            # Match either so a per-agent subscriber actually receives the
            # tool calls that make up its live trace (without this, only
            # tick_start/end pass the filter and the trace looks empty).
            data = event.data or {}
            event_agent = data.get("agent_id") or data.get("agent")
            if agent_filter and event_agent != agent_filter:
                continue
            try:
                loop.call_soon_threadsafe(queue.put_nowait, payload)
            except (RuntimeError, asyncio.QueueFull):
                pass  # Loop closed or client is slow

    def _on_console_event(event: Event) -> None:
        payload = console_history.add(_safe_console_payload(event))
        for ws, (queue, loop) in list(console_clients.items()):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, payload)
            except (RuntimeError, asyncio.QueueFull):
                pass

    # Subscribe to all agent events
    for event_type in _AGENT_EVENTS:
        event_bus.subscribe(event_type, _on_event)
    for event_type in _CONSOLE_EVENTS:
        event_bus.subscribe(event_type, _on_console_event)

    @router.websocket("/v1/agents/events")
    async def agent_events(websocket: WebSocket) -> None:
        from openjarvis.server.auth_middleware import websocket_authorized

        expected_key = getattr(websocket.app.state, "api_key", "")
        if not websocket_authorized(websocket, expected_key):
            # 1008 = policy violation; reject before accepting the connection.
            await websocket.close(code=1008)
            return
        await websocket.accept()
        # Parse agent_id filter from query string
        agent_id = websocket.query_params.get("agent_id")
        websocket._agent_filter = agent_id  # type: ignore[attr-defined]
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        loop = asyncio.get_running_loop()
        clients[websocket] = (queue, loop)
        try:
            while True:
                payload = await queue.get()
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        finally:
            clients.pop(websocket, None)

    @router.websocket("/v1/system/events")
    async def system_events(websocket: WebSocket) -> None:
        """Stream a sanitized operator event feed to the browser console."""
        from openjarvis.server.auth_middleware import websocket_authorized

        expected_key = getattr(websocket.app.state, "api_key", "")
        if not websocket_authorized(websocket, expected_key):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=250)
        loop = asyncio.get_running_loop()
        console_clients[websocket] = (queue, loop)
        try:
            replay_limit = websocket.query_params.get("history_limit", "2000")
            try:
                replay_limit_value = int(replay_limit)
            except ValueError:
                replay_limit_value = 2_000
            for payload in console_history.snapshot(replay_limit_value):
                await websocket.send_json(payload)
            while True:
                payload = await queue.get()
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        finally:
            console_clients.pop(websocket, None)

    @router.get("/v1/system/events/history")
    async def system_event_history(limit: int = 2_000) -> dict[str, Any]:
        """Return recent sanitized console events for history views."""
        events = console_history.snapshot(limit)
        return {"events": events, "count": len(events), "limit": max(0, min(limit, 2_000))}

    return router


__all__ = ["ConsoleEventHistory", "create_ws_router"]
