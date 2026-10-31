"""A confirmation-gated bridge to a local Home Assistant instance.

The bridge supports two deliberately constrained paths:

* Alexa-style phrases sent to the configured Kitchen Echo Dot.
* Direct controls for Home Assistant lights, switches, fans, scenes, and
  media players, using their existing entity IDs.

It does not expose arbitrary Home Assistant services or endpoints.
"""

from __future__ import annotations

import logging
import json
import os
from typing import Any

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://127.0.0.1:8123"
_DEFAULT_ALEXA_DEVICES = {
    "kitchen": {
        "id": "ac47606d23d7b56afc8687a71501cc89",
        "name": "Kitchen Echo Dot",
    },
    "bedroom": {
        "id": "3deed799cc53eb1274c8a0b08bdab124",
        "name": "Bedroom Speaker",
    },
}
_DIRECT_CONTROL_DOMAINS = {"light", "switch", "fan", "scene"}


def get_alexa_devices() -> dict[str, dict[str, str]]:
    """Return the local Alexa device catalog, merging optional user entries.

    ``HOME_ASSISTANT_ALEXA_DEVICES`` may contain a JSON object keyed by a
    simple target name.  Each value needs an ``id`` (Home Assistant device ID)
    and a human-friendly ``name``.  Bad entries are ignored rather than making
    local home control unavailable.
    """
    devices = {key: value.copy() for key, value in _DEFAULT_ALEXA_DEVICES.items()}
    raw = os.getenv("HOME_ASSISTANT_ALEXA_DEVICES", "").strip()
    if not raw:
        return devices
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid HOME_ASSISTANT_ALEXA_DEVICES JSON")
        return devices
    if not isinstance(configured, dict):
        return devices
    for key, value in configured.items():
        target = str(key).strip().lower()
        if not target or not isinstance(value, dict):
            continue
        device_id = str(value.get("id", "")).strip()
        name = str(value.get("name", target)).strip()
        if device_id and name:
            devices[target] = {"id": device_id, "name": name}
    return devices


@ToolRegistry.register("home_assistant_command")
class HomeAssistantCommandTool(BaseTool):
    """Run a confirmed, approved command through Home Assistant."""

    tool_id = "home_assistant_command"
    is_local = False

    def __init__(
        self,
        *,
        allowed_alexa_targets: set[str] | None = None,
        allow_bedroom: bool = True,
    ) -> None:
        self._allowed_alexa_targets = allowed_alexa_targets
        self._allow_bedroom = allow_bedroom

    @property
    def spec(self) -> ToolSpec:
        devices = get_alexa_devices()
        targets = sorted(
            key
            for key in devices
            if self._allowed_alexa_targets is None
            or key in self._allowed_alexa_targets
        )
        return ToolSpec(
            name=self.tool_id,
            description=(
                "Control a configured Alexa speaker or an existing Home "
                "Assistant light, switch, fan, scene, or media player. Every "
                "command requires the user's confirmation before it is sent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "alexa_text",
                            "turn_on",
                            "turn_off",
                            "toggle",
                            "set_volume",
                        ],
                        "description": (
                            "Use alexa_text for a phrase sent to a configured "
                            "Alexa speaker. Use the other operations for a direct Home "
                            "Assistant entity control."
                        ),
                    },
                    "text_command": {
                        "type": "string",
                        "description": (
                            "The phrase to send to an Alexa speaker, such as "
                            "'play rain sounds', 'stop', or 'set volume to 3'."
                        ),
                    },
                    "target": {
                        "type": "string",
                        "enum": targets,
                        "description": (
                            "Speaker to receive an alexa_text command. Defaults "
                            "to kitchen."
                        ),
                    },
                    "entity_id": {
                        "type": "string",
                        "description": (
                            "A Home Assistant entity ID for direct control, such "
                            "as light.kitchen, switch.coffee_maker, or "
                            "media_player.kitchen_echo_dot."
                        ),
                    },
                    "brightness_pct": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Optional brightness percentage for a light turn_on command.",
                    },
                    "color_name": {
                        "type": "string",
                        "description": "Optional color name for a light turn_on command.",
                    },
                    "volume_level": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "Media-player volume from 0 (mute) to 1 (maximum).",
                    },
                },
                "required": ["operation"],
            },
            category="home_automation",
            requires_confirmation=True,
            timeout_seconds=15.0,
            required_capabilities=["home:control"],
        )

    def execute(self, **params: Any) -> ToolResult:
        operation = str(params.get("operation", "")).strip()
        if operation == "alexa_text":
            return self._send_alexa_text(params)
        if operation in {"turn_on", "turn_off", "toggle"}:
            return self._control_entity(operation, params)
        if operation == "set_volume":
            return self._set_volume(params)
        return ToolResult(
            tool_name=self.tool_id,
            content=f"Unsupported Home Assistant operation: {operation or 'none'}.",
            success=False,
        )

    def _send_alexa_text(self, params: dict[str, Any]) -> ToolResult:
        text_command = str(params.get("text_command", "")).strip()
        if not text_command:
            return ToolResult(
                tool_name=self.tool_id,
                content="No Home Assistant command was provided.",
                success=False,
            )

        target = str(params.get("target", "kitchen")).strip().lower()
        devices = get_alexa_devices()
        if (
            self._allowed_alexa_targets is not None
            and target not in self._allowed_alexa_targets
        ):
            return ToolResult(
                tool_name=self.tool_id,
                content=f"The '{target}' speaker is not available for this request.",
                success=False,
            )
        device = devices.get(target)
        if device is None:
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"Unknown Alexa speaker '{target}'. Available speakers: "
                    f"{', '.join(sorted(devices))}."
                ),
                success=False,
            )

        return self._call_service(
            "alexa_devices",
            "send_text_command",
            {"device_id": device["id"], "text_command": text_command},
            success_message=f"Sent to the {device['name']}: {text_command}",
            metadata={"device": device["name"], "target": target},
        )

    def _control_entity(self, operation: str, params: dict[str, Any]) -> ToolResult:
        entity_id = str(params.get("entity_id", "")).strip()
        if not self._allow_bedroom and "bedroom" in entity_id.lower():
            return ToolResult(
                tool_name=self.tool_id,
                content="The bedroom device is not available for this request.",
                success=False,
            )
        domain = self._validated_domain(entity_id, _DIRECT_CONTROL_DOMAINS)
        if not domain:
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    "Direct controls require a light, switch, fan, or scene "
                    "entity ID, for example light.kitchen."
                ),
                success=False,
            )
        if domain == "scene" and operation != "turn_on":
            return ToolResult(
                tool_name=self.tool_id,
                content="Scenes support turn_on only.",
                success=False,
            )

        data: dict[str, Any] = {"entity_id": entity_id}
        if operation == "turn_on" and domain == "light":
            brightness = params.get("brightness_pct")
            if brightness is not None:
                try:
                    brightness = int(brightness)
                except (TypeError, ValueError):
                    return self._invalid_brightness()
                if not 1 <= brightness <= 100:
                    return self._invalid_brightness()
                data["brightness_pct"] = brightness
            color_name = str(params.get("color_name", "")).strip()
            if color_name:
                data["color_name"] = color_name

        return self._call_service(
            domain,
            operation,
            data,
            success_message=f"Requested {operation} for {entity_id}.",
            metadata={"entity_id": entity_id},
        )

    def _set_volume(self, params: dict[str, Any]) -> ToolResult:
        entity_id = str(params.get("entity_id", "")).strip()
        if not self._allow_bedroom and "bedroom" in entity_id.lower():
            return ToolResult(
                tool_name=self.tool_id,
                content="The bedroom device is not available for this request.",
                success=False,
            )
        if self._validated_domain(entity_id, {"media_player"}) != "media_player":
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    "Setting volume requires a media-player entity ID, for "
                    "example media_player.kitchen_echo_dot."
                ),
                success=False,
            )
        try:
            volume = float(params.get("volume_level"))
        except (TypeError, ValueError):
            volume = -1
        if not 0 <= volume <= 1:
            return ToolResult(
                tool_name=self.tool_id,
                content="volume_level must be a number from 0 to 1.",
                success=False,
            )
        return self._call_service(
            "media_player",
            "volume_set",
            {"entity_id": entity_id, "volume_level": volume},
            success_message=f"Set volume for {entity_id} to {volume:.0%}.",
            metadata={"entity_id": entity_id},
        )

    def _call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        *,
        success_message: str,
        metadata: dict[str, Any],
    ) -> ToolResult:
        token = os.getenv("HOME_ASSISTANT_TOKEN", "").strip()
        if not token:
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    "Home Assistant is not connected. Set HOME_ASSISTANT_TOKEN "
                    "to a Home Assistant long-lived access token, then restart "
                    "OpenJarvis."
                ),
                success=False,
            )

        base_url = os.getenv("HOME_ASSISTANT_URL", _DEFAULT_URL).strip().rstrip("/")
        endpoint = f"{base_url}/api/services/{domain}/{service}"
        try:
            response = httpx.post(
                endpoint,
                headers={"Authorization": f"Bearer {token}"},
                json=data,
                timeout=15.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Home Assistant rejected a command: %s", exc)
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    "Home Assistant rejected the command "
                    f"(HTTP {exc.response.status_code})."
                ),
                success=False,
            )
        except httpx.RequestError as exc:
            logger.warning("Home Assistant request failed: %s", exc)
            return ToolResult(
                tool_name=self.tool_id,
                content="Could not reach Home Assistant at the configured URL.",
                success=False,
            )

        return ToolResult(
            tool_name=self.tool_id,
            content=success_message,
            metadata={"service": f"{domain}.{service}", **metadata},
        )

    @staticmethod
    def _validated_domain(entity_id: str, allowed_domains: set[str]) -> str:
        domain, separator, object_id = entity_id.partition(".")
        if not separator or not object_id or domain not in allowed_domains:
            return ""
        return domain

    def _invalid_brightness(self) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content="brightness_pct must be a whole number from 1 to 100.",
            success=False,
        )


__all__ = ["HomeAssistantCommandTool", "get_alexa_devices"]
