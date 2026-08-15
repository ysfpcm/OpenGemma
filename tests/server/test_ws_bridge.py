"""Tests for WebSocket event bridge."""

from __future__ import annotations

import time

import pytest

from openjarvis.core.events import Event, EventBus, EventType

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def app(event_bus):
    from openjarvis.server.ws_bridge import create_ws_router

    app = FastAPI()
    router = create_ws_router(event_bus)
    app.include_router(router)
    return app


class TestWSBridge:
    def test_camera_console_payload_is_sanitized(self):
        from openjarvis.server.ws_bridge import _safe_console_payload

        payload = _safe_console_payload(
            Event(
                event_type=EventType.SNAPSHOT_RECEIVED,
                timestamp=time.time(),
                data={
                    "source": "home_assistant",
                    "entity_name": "Front Door",
                    "snapshot_ref": "context://snapshots/front.jpg",
                    "snapshot_url": "https://example.invalid/snapshot?token=secret",
                    "sdp": "v=0 secret stream",
                },
            )
        )
        assert payload["category"] == "cameras"
        assert payload["data"] == {
            "source": "home_assistant",
            "entity_name": "Front Door",
            "snapshot_ref": "context://snapshots/front.jpg",
        }

    def test_context_update_console_payload_is_sanitized(self):
        from openjarvis.server.ws_bridge import _safe_console_payload

        payload = _safe_console_payload(
            Event(
                event_type=EventType.CONTEXT_UPDATED,
                timestamp=time.time(),
                data={
                    "source": "home_assistant",
                    "context_event_id": 42,
                    "event_type": "state_changed",
                    "entity_name": "Front Person",
                    "entity_type": "binary_sensor",
                    "changed_state_keys": ["state"],
                    "changed_state_count": 1,
                    "secret": "must not pass",
                },
            )
        )

        assert payload["category"] == "context"
        assert payload["summary"] == "context updated · Front Person"
        assert payload["data"] == {
            "source": "home_assistant",
            "context_event_id": 42,
            "event_type": "state_changed",
            "entity_name": "Front Person",
            "entity_type": "binary_sensor",
            "changed_state_count": 1,
        }

    def test_websocket_receives_events(self, app, event_bus):
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events") as ws:
            event_bus.publish(
                EventType.AGENT_TICK_START,
                {
                    "agent_id": "test-123",
                    "agent_name": "test",
                },
            )
            time.sleep(0.05)  # Let call_soon_threadsafe deliver to queue
            data = ws.receive_json()
            assert data["type"] == "agent_tick_start"
            assert data["data"]["agent_id"] == "test-123"

    def test_websocket_replays_recent_console_history(self, app, event_bus):
        client = TestClient(app)
        event_bus.publish(
            EventType.CONTEXT_UPDATED,
            {"source": "home_assistant", "entity_name": "Front Door"},
        )
        with client.websocket_connect("/v1/system/events") as ws:
            data = ws.receive_json()
            assert data["type"] == "context_updated"
            assert data["id"] == 1
            assert data["data"]["entity_name"] == "Front Door"

    def test_history_endpoint_returns_sanitized_events(self, app, event_bus):
        client = TestClient(app)
        event_bus.publish(
            EventType.SNAPSHOT_RECEIVED,
            {
                "source": "home_assistant",
                "entity_name": "Front Door",
                "snapshot_ref": "context://snapshots/front.jpg",
                "snapshot_url": "https://example.invalid/snapshot?token=secret",
            },
        )
        response = client.get("/v1/system/events/history?limit=1")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["events"][0]["data"] == {
            "source": "home_assistant",
            "entity_name": "Front Door",
            "snapshot_ref": "context://snapshots/front.jpg",
        }

    def test_websocket_filters_by_agent_id(self, app, event_bus):
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events?agent_id=agent-A") as ws:
            # This event should NOT be received (different agent)
            event_bus.publish(EventType.AGENT_TICK_START, {"agent_id": "agent-B"})
            # This event SHOULD be received
            event_bus.publish(EventType.AGENT_TICK_START, {"agent_id": "agent-A"})
            time.sleep(0.05)  # Let call_soon_threadsafe deliver to queue
            data = ws.receive_json()
            assert data["data"]["agent_id"] == "agent-A"
