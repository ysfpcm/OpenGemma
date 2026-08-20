from types import SimpleNamespace

from openjarvis.server.departure_watcher_chat import (
    arm_departure_watcher_from_chat,
    format_departure_watcher_chat_result,
    parse_departure_watcher_request,
)
from openjarvis.server.models import ChatCompletionRequest
from openjarvis.server.routes import _try_arm_departure_watcher_from_chat


class _FakeWatcherService:
    def __init__(self):
        self.calls = []

    def create_watcher(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "watcher_id": "departure-watcher-chat-test",
            "current_status": "ACTIVE",
            "expiration_time": "2030-01-01T00:15:00Z",
            "mode": kwargs["mode"],
        }


def _request(service):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(departure_watcher_service=service)
        ),
        headers={},
    )


def test_parser_recognizes_departure_request_and_defaults_to_15_minutes():
    intent = parse_departure_watcher_request(
        "I'm leaving soon. Watch for me walking out the front door and turn off "
        "the Living Room Lamp."
    )

    assert intent is not None
    assert intent.duration_seconds == 900
    assert intent.live_requested is False


def test_parser_recognizes_camera_sees_me_wording():
    intent = parse_departure_watcher_request(
        "I am leaving in 15 minutes. When the front door camera sees me leave, "
        "turn off only the Living Room Lamp."
    )

    assert intent is not None
    assert intent.duration_seconds == 900
    assert intent.live_requested is False


def test_chat_path_creates_durable_simulation_watcher_with_conversation_id():
    service = _FakeWatcherService()
    request_body = ChatCompletionRequest(
        model="qwen3.5:4b",
        conversation_id="ophanim-phone-conversation-1",
        messages=[
            {
                "role": "user",
                "content": (
                    "Arm a watcher for the next 5 minutes. When you see me "
                    "walk out the front door, turn off the Living Room Lamp."
                ),
            }
        ],
        stream=True,
    )

    intercepted = _try_arm_departure_watcher_from_chat(request_body, _request(service))

    assert intercepted is not None
    content, result = intercepted
    assert result["armed"] is True
    assert "SIMULATION" in content
    assert service.calls == [
        {
            "conversation_id": "ophanim-phone-conversation-1",
            "duration_seconds": 300,
            "mode": "simulation",
            "live_approved": False,
        }
    ]


def test_live_request_is_not_armed_by_chat_path():
    service = _FakeWatcherService()
    intent = parse_departure_watcher_request(
        "Arm it for real: watch the front door and actually turn off the "
        "Living Room Lamp when I leave."
    )
    assert intent is not None

    result = arm_departure_watcher_from_chat(
        intent=intent,
        service=service,
        conversation_id="conversation-live-test",
    )

    assert result["armed"] is False
    assert result["status"] == "NEEDS_ATTENTION"
    assert service.calls == []
    assert "did not arm" in format_departure_watcher_chat_result(result)


def test_explicit_live_approval_arms_the_exact_live_watcher_from_chat():
    service = _FakeWatcherService()
    intent = parse_departure_watcher_request(
        "Arm a live watcher for 5 minutes. I explicitly approve live execution: "
        "when the front door camera sees me leave, actually turn off only the "
        "Living Room Lamp."
    )
    assert intent is not None
    assert intent.live_requested is True
    assert intent.live_approved is True

    result = arm_departure_watcher_from_chat(
        intent=intent,
        service=service,
        conversation_id="conversation-live-approved-test",
    )

    assert result["armed"] is True
    assert service.calls == [
        {
            "conversation_id": "conversation-live-approved-test",
            "duration_seconds": 300,
            "mode": "live",
            "live_approved": True,
        }
    ]
    assert "LIVE mode" in format_departure_watcher_chat_result(result)


def test_non_watcher_question_is_left_for_normal_chat():
    service = _FakeWatcherService()
    request_body = ChatCompletionRequest(
        model="qwen3.5:4b",
        messages=[
            {
                "role": "user",
                "content": "What does simulation mode mean for the Living Room Lamp?",
            }
        ],
        stream=True,
    )

    assert _try_arm_departure_watcher_from_chat(request_body, _request(service)) is None
    assert service.calls == []
