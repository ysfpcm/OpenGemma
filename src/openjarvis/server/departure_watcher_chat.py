"""Deterministic chat-intent handling for one-time departure watchers.

The main OpenJarvis chat UI uses ``/v1/chat/completions`` directly.  This
module recognizes the narrow, safety-critical departure-watcher request before
the language model is called, so arming a watcher does not depend on an agent
selecting or successfully invoking a tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DepartureWatcherChatIntent:
    """A validated request to arm the current departure watcher."""

    duration_seconds: int = 900
    live_requested: bool = False
    live_approved: bool = False


_ARMING_TERMS = (
    "arm",
    "set up",
    "setup",
    "watch",
    "monitor",
    "listen",
    "as soon as",
    "when you see",
    "when i leave",
    "when i walk",
    "camera sees",
    "sees me",
)
_DEPARTURE_TERMS = (
    "leave",
    "leaving",
    "walk out",
    "walk outside",
    "exit",
    "depart",
    "front door",
    "doorbell",
    "door camera",
)
_LAMP_TERMS = (
    "living room lamp",
    "living-room lamp",
    "livingroom lamp",
)
_SIMULATION_TERMS = ("simulation", "simulate", "dry run", "no live", "do not execute")
_LIVE_TERMS = (
    "live mode",
    "actually turn off",
    "actually do it",
    "for real",
    "execute it",
    "make the change",
)
_EXPLICIT_APPROVAL_TERMS = (
    "i approve",
    "i explicitly approve",
    "my explicit approval",
    "with my approval",
    "marc approves",
)


def parse_departure_watcher_request(text: str) -> DepartureWatcherChatIntent | None:
    """Recognize the supported natural-language departure request.

    This intentionally recognizes only the current bounded action.  A normal
    question about departure watchers, simulation, or lamps will not arm one
    unless it also contains both arming/departure language and the exact
    Living Room Lamp target.
    """

    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not normalized:
        return None
    if not any(term in normalized for term in _ARMING_TERMS):
        return None
    if not any(term in normalized for term in _DEPARTURE_TERMS):
        return None
    if not any(term in normalized for term in _LAMP_TERMS):
        return None

    duration_seconds = 900
    match = re.search(r"\b(\d{1,3})\s*(?:minutes?|mins?)\b", normalized)
    if match:
        minutes = max(1, min(15, int(match.group(1))))
        duration_seconds = minutes * 60

    live_requested = any(term in normalized for term in _LIVE_TERMS)
    # Explicit simulation wording always wins over incidental wording such as
    # “actually see me walk out,” which describes the trigger rather than a
    # request to perform a live device write.
    if any(term in normalized for term in _SIMULATION_TERMS):
        live_requested = False

    # A live watcher is an unattended effect. A live phrase is only a request;
    # it becomes approved when the same command plainly says Marc approves it.
    # The parser is deliberately constrained to the one target and trigger.
    live_approved = live_requested and any(
        term in normalized for term in _EXPLICIT_APPROVAL_TERMS
    )

    return DepartureWatcherChatIntent(
        duration_seconds=duration_seconds,
        live_requested=live_requested,
        live_approved=live_approved,
    )


def arm_departure_watcher_from_chat(
    *,
    intent: DepartureWatcherChatIntent,
    service: Any | None,
    conversation_id: str,
) -> dict[str, Any]:
    """Arm the narrowly-scoped watcher after deterministic intent parsing."""

    if intent.live_requested and not intent.live_approved:
        return {
            "armed": False,
            "status": "NEEDS_ATTENTION",
            "reason": (
                "Live execution was requested without an explicit Marc approval "
                "in the same command. No watcher was armed."
            ),
        }
    if service is None:
        return {
            "armed": False,
            "status": "NEEDS_ATTENTION",
            "reason": (
                "The durable departure-watcher service is unavailable. No "
                "future event monitoring was armed."
            ),
        }

    try:
        report = service.create_watcher(
            conversation_id=conversation_id,
            duration_seconds=int(intent.duration_seconds),
            mode="live" if intent.live_approved else "simulation",
            live_approved=bool(intent.live_approved),
        )
    except Exception as exc:  # noqa: BLE001 - chat must fail closed
        return {
            "armed": False,
            "status": "NEEDS_ATTENTION",
            "reason": (
                "The departure watcher could not be armed, so no future "
                f"event monitoring was claimed: {type(exc).__name__}."
            ),
        }

    status = str(report.get("current_status") or report.get("status") or "")
    return {
        "armed": status == "ACTIVE",
        "status": status or "NEEDS_ATTENTION",
        "report": report,
    }


def format_departure_watcher_chat_result(result: dict[str, Any]) -> str:
    """Render a concise, unambiguous assistant response for the chat UI."""

    if not result.get("armed"):
        return (
            "I did not arm the departure watcher. "
            f"Status: {result.get('status', 'NEEDS_ATTENTION')}. "
            f"{result.get('reason', 'No future events will be monitored.')}"
        )

    report = result.get("report") or {}
    watcher_id = report.get("watcher_id", "unknown")
    expires = report.get("expiration_time", "unknown")
    mode = str(report.get("mode") or "simulation").upper()
    action = (
        "send exactly `light.turn_off` to `light.living_room_lamp` only after "
        "Guardian authorization and independent Home Assistant verification."
        if mode == "LIVE"
        else (
            "prepare turning off only the Living Room Lamp; no Home Assistant "
            "write will be sent."
        )
    )
    return (
        f"Departure watcher armed in {mode} mode.\n"
        f"Watcher ID: {watcher_id}\n"
        "Trigger: one new front-door or doorbell-camera event received after "
        "arming, subject to timestamp, camera, occupancy, and conflict checks.\n"
        f"Expires: {expires}\n"
        f"Action: {action}\n"
        "It will fire once and persist its evidence and lifecycle records."
    )


__all__ = [
    "DepartureWatcherChatIntent",
    "arm_departure_watcher_from_chat",
    "format_departure_watcher_chat_result",
    "parse_departure_watcher_request",
]
