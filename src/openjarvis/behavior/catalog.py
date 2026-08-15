"""Canonical intent catalog shared by the model, resolver, and HA tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IntentSpec:
    """One allow-listed action Ophanim may extract and execute."""

    action: str
    description: str
    domains: tuple[str, ...]
    service: str | None = None
    parameters: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()
    verification: str = "state"
    examples: tuple[str, ...] = ()


def _spec(
    action: str,
    description: str,
    domains: tuple[str, ...],
    *,
    service: str | None = None,
    parameters: tuple[str, ...] = (),
    required_parameters: tuple[str, ...] = (),
    verification: str = "state",
    examples: tuple[str, ...] = (),
) -> IntentSpec:
    return IntentSpec(
        action=action,
        description=description,
        domains=domains,
        service=service,
        parameters=parameters,
        required_parameters=required_parameters,
        verification=verification,
        examples=examples,
    )


INTENT_CATALOG: dict[str, IntentSpec] = {
    "turn_on": _spec(
        "turn_on",
        "Turn on a controllable device.",
        ("light", "switch", "fan", "input_boolean", "group"),
        service="turn_on",
        examples=("turn it on", "wake up the device", "power up the plug"),
    ),
    "turn_off": _spec(
        "turn_off",
        "Turn off a controllable device.",
        ("light", "switch", "fan", "input_boolean", "group"),
        service="turn_off",
        examples=("turn it off", "shut it down", "kill the light"),
    ),
    "toggle": _spec(
        "toggle",
        "Toggle a controllable device.",
        ("light", "switch", "fan", "input_boolean", "group"),
        service="toggle",
        examples=("toggle the lamp", "flip that switch"),
    ),
    "get_state": _spec(
        "get_state",
        "Read the current state of a device.",
        ("*",),
        verification="read",
        examples=("is it on", "check that device", "what is its status"),
    ),
    "set_brightness": _spec(
        "set_brightness",
        "Set a light brightness percentage.",
        ("light",),
        service="turn_on",
        parameters=("brightness_pct",),
        required_parameters=("brightness_pct",),
        examples=("set the lamp to 40 percent", "dim the light to 20"),
    ),
    "set_color": _spec(
        "set_color",
        "Set a light color.",
        ("light",),
        service="turn_on",
        parameters=("color",),
        required_parameters=("color",),
        examples=("make the lamp red", "set the light to blue"),
    ),
    "set_color_temperature": _spec(
        "set_color_temperature",
        "Set a light color temperature in Kelvin.",
        ("light",),
        service="turn_on",
        parameters=("color_temp_kelvin",),
        required_parameters=("color_temp_kelvin",),
        examples=("make the light warmer", "set the lamp to 3000 Kelvin"),
    ),
    "set_temperature": _spec(
        "set_temperature",
        "Set a climate target temperature.",
        ("climate",),
        service="set_temperature",
        parameters=("temperature",),
        required_parameters=("temperature",),
        examples=("set the thermostat to 70", "make it 21 degrees"),
    ),
    "set_hvac_mode": _spec(
        "set_hvac_mode",
        "Set a climate mode such as heat, cool, or auto.",
        ("climate",),
        service="set_hvac_mode",
        parameters=("hvac_mode",),
        required_parameters=("hvac_mode",),
        examples=("put the thermostat on cool", "switch to heat"),
    ),
    "set_fan_speed": _spec(
        "set_fan_speed",
        "Set a fan percentage.",
        ("fan",),
        service="set_percentage",
        parameters=("percentage",),
        required_parameters=("percentage",),
        examples=("set the fan to 50 percent", "run the fan halfway"),
    ),
    "media_play": _spec("media_play", "Resume media playback.", ("media_player",), service="media_play", examples=("play it", "resume the TV")),
    "media_pause": _spec("media_pause", "Pause media playback.", ("media_player",), service="media_pause", examples=("pause the TV", "hold the music")),
    "media_stop": _spec("media_stop", "Stop media playback.", ("media_player",), service="media_stop", verification="ack", examples=("stop the music", "stop playback")),
    "media_next": _spec("media_next", "Skip to the next media item.", ("media_player",), service="media_next_track", verification="ack", examples=("next song", "skip this track")),
    "media_previous": _spec("media_previous", "Go to the previous media item.", ("media_player",), service="media_previous_track", verification="ack", examples=("previous song", "go back one track")),
    "set_volume": _spec(
        "set_volume",
        "Set media player volume from 0 to 100 percent.",
        ("media_player",),
        service="volume_set",
        parameters=("volume_pct",),
        required_parameters=("volume_pct",),
        examples=("set the volume to 30", "make it quieter at 20 percent"),
    ),
    "open_cover": _spec("open_cover", "Open blinds, shades, curtains, or a cover.", ("cover",), service="open_cover", examples=("open the blinds", "raise the shade")),
    "close_cover": _spec("close_cover", "Close blinds, shades, curtains, or a cover.", ("cover",), service="close_cover", examples=("close the blinds", "lower the shade")),
    "stop_cover": _spec("stop_cover", "Stop a moving cover.", ("cover",), service="stop_cover", verification="ack", examples=("stop the blinds", "hold the curtain")),
    "set_cover_position": _spec(
        "set_cover_position",
        "Set a cover position from 0 to 100 percent.",
        ("cover",),
        service="set_cover_position",
        parameters=("position",),
        required_parameters=("position",),
        examples=("open the blinds halfway", "set the shade to 70 percent"),
    ),
    "lock": _spec("lock", "Lock a lockable device.", ("lock",), service="lock", examples=("lock the front door", "secure the door")),
    "unlock": _spec("unlock", "Unlock a lockable device.", ("lock",), service="unlock", examples=("unlock the front door", "open the lock")),
    "activate_scene": _spec("activate_scene", "Activate a Home Assistant scene.", ("scene",), service="turn_on", verification="ack", examples=("activate movie mode", "set the house to bedtime")),
    "run_script": _spec("run_script", "Run a Home Assistant script.", ("script",), service="turn_on", verification="ack", examples=("run the goodnight script", "start my morning routine")),
    "enable_automation": _spec("enable_automation", "Enable an automation.", ("automation",), service="turn_on", examples=("enable that automation", "turn on the automation")),
    "disable_automation": _spec("disable_automation", "Disable an automation.", ("automation",), service="turn_off", examples=("disable that automation", "turn off the automation")),
    "trigger_automation": _spec("trigger_automation", "Trigger an automation immediately.", ("automation",), service="trigger", verification="ack", examples=("run the arrival automation", "trigger that automation")),
    "get_temperature": _spec("get_temperature", "Read a temperature sensor or climate temperature.", ("sensor", "climate"), verification="read", examples=("what is the temperature", "how hot is it")),
    "get_humidity": _spec("get_humidity", "Read a humidity sensor.", ("sensor", "climate"), verification="read", examples=("what is the humidity", "how humid is it")),
    "get_battery": _spec("get_battery", "Read a device battery level.", ("sensor", "binary_sensor", "*"), verification="read", examples=("how much battery is left", "check the battery")),
    "get_motion": _spec("get_motion", "Read a motion or occupancy sensor.", ("binary_sensor", "sensor"), verification="read", examples=("is there motion", "did the motion sensor trigger")),
}

SUPPORTED_ACTIONS = frozenset(INTENT_CATALOG)


def get_intent_spec(action: str) -> IntentSpec | None:
    return INTENT_CATALOG.get(str(action or "").strip().lower())


def intent_schema() -> dict[str, Any]:
    """Return a JSON-schema-like contract suitable for a model prompt."""
    return {
        "type": "object",
        "properties": {
            "action": {"type": ["string", "null"], "enum": sorted(SUPPORTED_ACTIONS) + [None]},
            "entity_id": {"type": ["string", "null"]},
            "entity_mention": {"type": ["string", "null"]},
            "parameters": {"type": "object"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string"},
        },
        "required": ["action", "entity_id", "entity_mention", "parameters", "confidence", "rationale"],
    }


__all__ = ["INTENT_CATALOG", "IntentSpec", "SUPPORTED_ACTIONS", "get_intent_spec", "intent_schema"]
