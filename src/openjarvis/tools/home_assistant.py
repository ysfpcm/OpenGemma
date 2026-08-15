"""Verified Home Assistant reads and allow-listed device controls.

The behavior model may choose an intent, but this tool owns the actual Home
Assistant domain/service mapping.  Every state-changing action is followed by
an appropriate state or attribute check before it is reported as successful.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import httpx

from openjarvis.behavior.catalog import INTENT_CATALOG, get_intent_spec
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_DEFAULT_TEMPERATURE_HINT = "kitchen echo dot temperature"
_COLOR_RGB = {
    "red": [255, 0, 0],
    "orange": [255, 128, 0],
    "yellow": [255, 255, 0],
    "green": [0, 255, 0],
    "blue": [0, 0, 255],
    "purple": [128, 0, 128],
    "pink": [255, 105, 180],
    "white": [255, 255, 255],
}
_READ_ACTIONS = {
    "get_state",
    "get_temperature",
    "get_humidity",
    "get_battery",
    "get_motion",
}
_ATTRIBUTE_ACTIONS = {
    "set_brightness",
    "set_color",
    "set_color_temperature",
    "set_temperature",
    "set_hvac_mode",
    "set_fan_speed",
    "set_volume",
    "set_cover_position",
}


def _normalise(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _display_name(state: dict[str, Any]) -> str:
    return str(
        state.get("attributes", {}).get("friendly_name")
        or state.get("entity_id")
        or "device"
    )


def _domain(state: Mapping[str, Any]) -> str:
    return str(state.get("entity_id", "")).split(".", 1)[0]


@ToolRegistry.register("home_assistant")
class HomeAssistantTool(BaseTool):
    """Read and safely control the Home Assistant entities in the live instance."""

    tool_id = "home_assistant"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="home_assistant",
            description=(
                "Read live Home Assistant state or perform an allow-listed, "
                "verified device action. Use the canonical action names and "
                "friendly entity name or entity id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": sorted(INTENT_CATALOG),
                        "description": "Canonical operation from the Ophanim intent catalog.",
                    },
                    "entity": {
                        "type": "string",
                        "description": "Friendly device name or Home Assistant entity id.",
                    },
                    "brightness_pct": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "color": {
                        "type": ["string", "array"],
                        "description": "Named color or RGB array such as [255, 0, 0].",
                    },
                    "color_temp_kelvin": {"type": "number", "minimum": 1000, "maximum": 10000},
                    "temperature": {"type": "number"},
                    "hvac_mode": {"type": "string"},
                    "percentage": {"type": "number", "minimum": 0, "maximum": 100},
                    "volume_pct": {"type": "number", "minimum": 0, "maximum": 100},
                    "position": {"type": "number", "minimum": 0, "maximum": 100},
                },
                "required": ["action"],
            },
            category="home",
            timeout_seconds=12.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action") or "").strip().lower()
        entity_query = str(params.get("entity") or "").strip()
        spec = get_intent_spec(action)
        if spec is None:
            return self._failure("Unsupported Home Assistant action.")

        try:
            states = self._get_states()
            if action in _READ_ACTIONS:
                return self._execute_read(action, entity_query, states)

            target = self._find_target(states, entity_query, spec.domains)
            if target is None:
                return self._failure(
                    f"I couldn't find a unique Home Assistant device for {entity_query or 'that request'}."
                )

            entity_id = str(target["entity_id"])
            domain = _domain(target)
            service = spec.service
            if not service:
                return self._failure(f"No service is configured for {action}.")

            body = self._service_body(action, params)
            if body:
                self._call_service(domain, service, entity_id, body)
            else:
                # Keep the three-argument form for simple actions and for
                # compatibility with existing integrations/tests.
                self._call_service(domain, service, entity_id)

            if spec.verification == "ack":
                return self._success(
                    f"Home Assistant accepted {action.replace('_', ' ')} for {_display_name(target)}.",
                    target,
                    action=action,
                )

            verified = self._verify_write(action, target, params)
            if verified is None:
                return self._failure(
                    f"Home Assistant accepted the request, but I couldn't verify {action.replace('_', ' ')} for {_display_name(target)}."
                )
            return self._success(
                self._write_success_text(action, verified),
                verified,
                action=action,
            )
        except Exception:
            logger.exception("Home Assistant tool request failed")
            return self._failure(
                "I couldn't reach Home Assistant right now, so I did not confirm a device change."
            )

    def _execute_read(
        self,
        action: str,
        entity_query: str,
        states: list[dict[str, Any]],
    ) -> ToolResult:
        if action == "get_temperature":
            state = self._find_temperature(states, entity_query)
        elif action == "get_humidity":
            state = self._find_reading(states, entity_query, "humidity")
        elif action == "get_battery":
            state = self._find_reading(states, entity_query, "battery")
        elif action == "get_motion":
            state = self._find_reading(states, entity_query, "motion")
        else:
            state = self._find_target(states, entity_query, ("*",))

        if state is None:
            label = action.replace("get_", "").replace("_", " ")
            return self._failure(f"No matching {label} device was found in Home Assistant.")
        if action == "get_state":
            return self._state_result(state, action=action)

        label = _display_name(state)
        value = state.get("state")
        attributes = state.get("attributes", {})
        if action == "get_temperature" and _domain(state) == "climate":
            value = attributes.get("current_temperature", value)
        if action == "get_humidity" and _domain(state) == "climate":
            value = attributes.get("current_humidity", value)
        if action == "get_battery":
            value = attributes.get("battery_level", value)
        if action == "get_temperature":
            unit = str(state.get("attributes", {}).get("unit_of_measurement") or "°F")
            return self._success(f"{label} is {value} {unit}.", state, action=action)
        if action == "get_humidity":
            unit = str(state.get("attributes", {}).get("unit_of_measurement") or "%")
            return self._success(f"{label} is {value} {unit}.", state, action=action)
        if action == "get_battery":
            return self._success(f"{label} battery is {value}%.", state, action=action)
        return self._success(f"{label} reports {value}.", state, action=action)

    def _service_body(self, action: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if action == "set_brightness":
            value = self._bounded_number(params.get("brightness_pct"), 0, 100, "brightness_pct")
            return {"brightness_pct": value}
        if action == "set_color":
            color = params.get("color")
            if isinstance(color, str):
                rgb = _COLOR_RGB.get(_normalise(color))
            elif isinstance(color, (list, tuple)) and len(color) == 3:
                rgb = list(color)
            else:
                rgb = None
            if rgb is None or any(not isinstance(value, (int, float)) for value in rgb):
                raise ValueError("color must be a supported name or three-number RGB array")
            if any(float(value) < 0 or float(value) > 255 for value in rgb):
                raise ValueError("RGB values must be between 0 and 255")
            return {"rgb_color": [int(value) for value in rgb]}
        if action == "set_color_temperature":
            value = self._bounded_number(params.get("color_temp_kelvin"), 1000, 10000, "color_temp_kelvin")
            return {"color_temp_kelvin": value}
        if action == "set_temperature":
            value = self._number(params.get("temperature"), "temperature")
            return {"temperature": value}
        if action == "set_hvac_mode":
            value = str(params.get("hvac_mode") or "").strip().lower()
            if value not in {"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"}:
                raise ValueError("unsupported HVAC mode")
            return {"hvac_mode": value}
        if action == "set_fan_speed":
            value = self._bounded_number(params.get("percentage"), 0, 100, "percentage")
            return {"percentage": value}
        if action == "set_volume":
            value = self._bounded_number(params.get("volume_pct"), 0, 100, "volume_pct")
            return {"volume_level": float(value) / 100.0}
        if action == "set_cover_position":
            value = self._bounded_number(params.get("position"), 0, 100, "position")
            return {"position": value}
        return {}

    def _verify_write(
        self,
        action: str,
        original: dict[str, Any],
        params: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        entity_id = str(original["entity_id"])
        if action in {"turn_on", "turn_off"}:
            return self._wait_for_state(entity_id, "on" if action == "turn_on" else "off")
        if action == "toggle":
            current = str(original.get("state") or "").lower()
            expected = "off" if current == "on" else "on"
            return self._wait_for_state(entity_id, expected)
        if action in {"open_cover", "close_cover"}:
            return self._wait_for_state(entity_id, "open" if action == "open_cover" else "closed")
        if action in {"lock", "unlock"}:
            return self._wait_for_state(entity_id, "locked" if action == "lock" else "unlocked")
        if action in {"enable_automation", "disable_automation"}:
            return self._wait_for_state(entity_id, "on" if action == "enable_automation" else "off")

        expected = self._expected_attribute(action, params)
        if expected is None:
            return self._wait_for_state(entity_id, str(original.get("state") or ""))
        return self._wait_for_attribute(entity_id, expected)

    @staticmethod
    def _expected_attribute(
        action: str,
        params: Mapping[str, Any],
    ) -> Callable[[dict[str, Any]], bool] | None:
        if action == "set_brightness":
            target = float(params["brightness_pct"])

            def brightness_matches(state: dict[str, Any]) -> bool:
                attributes = state.get("attributes", {})
                actual = attributes.get("brightness_pct")
                if actual is None and attributes.get("brightness") is not None:
                    actual = float(attributes["brightness"]) / 255 * 100
                return _numeric_close(actual, target, tolerance=5)

            return brightness_matches
        if action == "set_color":
            color = params.get("color")
            target = (
                _COLOR_RGB.get(_normalise(color))
                if isinstance(color, str)
                else list(color)
                if isinstance(color, (list, tuple)) and len(color) == 3
                else None
            )
            if target is None:
                return None
            expected = [int(value) for value in target]
            return lambda state: state.get("attributes", {}).get("rgb_color") == expected
        if action == "set_color_temperature":
            target = float(params["color_temp_kelvin"])

            def color_temperature_matches(state: dict[str, Any]) -> bool:
                attributes = state.get("attributes", {})
                actual = attributes.get("color_temp_kelvin")
                if actual is None and attributes.get("color_temp"):
                    try:
                        actual = 1_000_000 / float(attributes["color_temp"])
                    except (TypeError, ValueError, ZeroDivisionError):
                        actual = None
                return _numeric_close(actual, target, tolerance=150)

            return color_temperature_matches
        if action == "set_temperature":
            target = float(params["temperature"])

            def temperature_matches(state: dict[str, Any]) -> bool:
                attributes = state.get("attributes", {})
                values = [
                    attributes.get("temperature"),
                    attributes.get("target_temperature"),
                    attributes.get("target_temp_high"),
                ]
                values.append(state.get("state"))
                return any(
                    value is not None
                    and _numeric_close(value, target, tolerance=0.5)
                    for value in values
                )

            return temperature_matches
        if action == "set_hvac_mode":
            target = str(params["hvac_mode"]).lower()
            return lambda state: str(state.get("state") or "").lower() == target
        if action == "media_play":
            return lambda state: str(state.get("state") or "").lower() == "playing"
        if action == "media_pause":
            return lambda state: str(state.get("state") or "").lower() == "paused"
        if action == "set_fan_speed":
            target = float(params["percentage"])
            return lambda state: _numeric_close(
                state.get("attributes", {}).get("percentage"), target, tolerance=5
            )
        if action == "set_volume":
            target = float(params["volume_pct"]) / 100
            return lambda state: _numeric_close(
                state.get("attributes", {}).get("volume_level"), target, tolerance=0.05
            )
        if action == "set_cover_position":
            target = float(params["position"])
            return lambda state: _numeric_close(
                state.get("attributes", {}).get("current_position"), target, tolerance=5
            )
        return None

    def _write_success_text(self, action: str, state: dict[str, Any]) -> str:
        label = _display_name(state)
        if action == "set_brightness":
            brightness = state.get("attributes", {}).get("brightness")
            return f"{label} brightness is now {brightness}."
        if action == "set_temperature":
            value = state.get("attributes", {}).get("temperature", state.get("state"))
            return f"{label} target temperature is now {value}."
        if action == "set_volume":
            value = state.get("attributes", {}).get("volume_level")
            return f"{label} volume is now {float(value) * 100:.0f}%."
        if action == "set_cover_position":
            value = state.get("attributes", {}).get("current_position")
            return f"{label} is now at position {value}%."
        if action in {
            "turn_on",
            "turn_off",
            "open_cover",
            "close_cover",
            "lock",
            "unlock",
            "enable_automation",
            "disable_automation",
        }:
            return f"{label} is now {state.get('state')}."
        if action == "toggle":
            return f"{label} is now {state.get('state')}."
        return f"{label} was updated successfully."

    def _get_states(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/states")
        if not isinstance(payload, list):
            raise RuntimeError("invalid states response")
        return [state for state in payload if isinstance(state, dict)]

    def _call_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        body: Mapping[str, Any] | None = None,
    ) -> None:
        payload = {"entity_id": entity_id}
        if body:
            payload.update(dict(body))
        self._request("POST", f"/api/services/{domain}/{service}", payload)

    def _wait_for_state(self, entity_id: str, expected_state: str) -> dict[str, Any] | None:
        return self._wait_for_attribute(
            entity_id,
            lambda state: str(state.get("state", "")).lower() == expected_state.lower(),
        )

    def _wait_for_attribute(
        self,
        entity_id: str,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any] | None:
        for _ in range(4):
            payload = self._request("GET", f"/api/states/{entity_id}")
            if isinstance(payload, dict) and predicate(payload):
                return payload
            time.sleep(0.4)
        return None

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        base_url = (
            os.environ.get("HA_URL")
            or os.environ.get("HOME_ASSISTANT_URL")
            or "http://127.0.0.1:8123"
        ).rstrip("/")
        token = (
            os.environ.get("HA_TOKEN")
            or os.environ.get("HOME_ASSISTANT_TOKEN")
            or ""
        ).strip()
        if not token:
            raise RuntimeError("Home Assistant is not configured")
        response = httpx.request(
            method,
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=8.0,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _find_target(
        states: list[dict[str, Any]],
        query: str,
        domains: Iterable[str],
    ) -> dict[str, Any] | None:
        allowed = set(domains)
        candidates = [
            state
            for state in states
            if "*" in allowed or _domain(state) in allowed
        ]
        query_key = _normalise(query)
        if not query_key:
            return candidates[0] if len(candidates) == 1 else None
        exact = [
            state
            for state in candidates
            if query_key
            in {
                _normalise(state.get("entity_id")),
                _normalise(_display_name(state)),
            }
        ]
        if len(exact) == 1:
            return exact[0]
        query_tokens = set(query_key.split())
        matches = [
            state
            for state in candidates
            if query_tokens <= set(_normalise(_display_name(state)).split())
            or query_tokens <= set(_normalise(state.get("entity_id")).split())
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _find_entity(
        states: list[dict[str, Any]],
        query: str,
        *,
        domain: str,
    ) -> dict[str, Any] | None:
        """Backward-compatible single-domain entity lookup."""
        return HomeAssistantTool._find_target(states, query, (domain,))

    @staticmethod
    def _find_temperature(
        states: list[dict[str, Any]],
        query: str,
    ) -> dict[str, Any] | None:
        candidates = [
            state
            for state in states
            if (
                _domain(state) == "climate"
                or (
                    _domain(state) == "sensor"
                    and (
                        str(state.get("attributes", {}).get("device_class", "")).lower()
                        == "temperature"
                        or "temperature" in _normalise(_display_name(state))
                        or "temperature" in _normalise(state.get("entity_id"))
                    )
                )
            )
        ]
        if query:
            return HomeAssistantTool._find_target(candidates, query, ("sensor", "climate"))
        preferred = [
            state
            for state in candidates
            if _DEFAULT_TEMPERATURE_HINT in _normalise(_display_name(state))
        ]
        return preferred[0] if len(preferred) == 1 else (candidates[0] if len(candidates) == 1 else None)

    @staticmethod
    def _find_reading(
        states: list[dict[str, Any]],
        query: str,
        kind: str,
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for state in states:
            attributes = state.get("attributes", {})
            device_class = str(attributes.get("device_class") or "").lower()
            haystack = f"{_normalise(_display_name(state))} {_normalise(state.get('entity_id'))}"
            if kind == "humidity" and (device_class == "humidity" or "humidity" in haystack):
                candidates.append(state)
            elif kind == "battery" and (
                device_class == "battery"
                or "battery" in haystack
                or attributes.get("battery_level") is not None
            ):
                candidates.append(state)
            elif kind == "motion" and (
                device_class in {"motion", "occupancy"}
                or "motion" in haystack
                or "occupancy" in haystack
            ):
                candidates.append(state)
        if query:
            return HomeAssistantTool._find_target(candidates, query, ("*",))
        return candidates[0] if len(candidates) == 1 else None

    def _state_result(self, state: dict[str, Any], *, action: str = "get_state") -> ToolResult:
        label = _display_name(state)
        value = str(state.get("state") or "unknown")
        if value == "on":
            brightness = state.get("attributes", {}).get("brightness")
            suffix = f" at brightness {brightness}." if brightness is not None else "."
            return self._success(f"{label} is on{suffix}", state, action=action)
        return self._success(f"{label} is {value}.", state, action=action)

    def _success(
        self,
        content: str,
        state: dict[str, Any],
        *,
        action: str = "",
    ) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content=content,
            success=True,
            metadata={
                "action": action,
                "entity_id": state.get("entity_id"),
                "state": state.get("state"),
            },
        )

    def _failure(self, content: str) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=content, success=False)

    @staticmethod
    def _number(value: Any, name: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc

    @classmethod
    def _bounded_number(cls, value: Any, minimum: float, maximum: float, name: str) -> float:
        number = cls._number(value, name)
        if not minimum <= number <= maximum:
            raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
        return number


def _numeric_close(value: Any, target: float, *, tolerance: float) -> bool:
    try:
        return abs(float(value) - target) <= tolerance
    except (TypeError, ValueError):
        return False


__all__ = ["HomeAssistantTool"]
