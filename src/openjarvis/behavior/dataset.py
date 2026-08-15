"""Catalog-driven seed data for natural Home Assistant behavior training."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from openjarvis.behavior.catalog import INTENT_CATALOG

_ENTITIES: dict[str, tuple[str, str]] = {
    "light": ("light.livingroomlamp", "living room lamp"),
    "switch": ("switch.coffee_maker", "coffee maker"),
    "fan": ("fan.bedroom", "bedroom fan"),
    "input_boolean": ("input_boolean.guest_mode", "guest mode"),
    "group": ("group.downstairs_lights", "downstairs lights"),
    "climate": ("climate.hallway", "hallway thermostat"),
    "media_player": ("media_player.living_room_tv", "living room TV"),
    "cover": ("cover.living_room_blinds", "living room blinds"),
    "lock": ("lock.front_door", "front door lock"),
    "scene": ("scene.movie_mode", "movie mode"),
    "script": ("script.goodnight", "goodnight routine"),
    "automation": ("automation.porch_lights", "porch lights automation"),
    "sensor_temperature": ("sensor.hallway_temperature", "hallway temperature sensor"),
    "sensor_humidity": ("sensor.bathroom_humidity", "bathroom humidity sensor"),
    "sensor_battery": ("sensor.phone_battery", "phone battery"),
    "binary_sensor_motion": ("binary_sensor.hall_motion", "hall motion sensor"),
}

_TEMPLATES: dict[str, tuple[str, ...]] = {
    "turn_on": (
        "turn on {entity}",
        "switch {entity} on",
        "power up {entity}",
        "wake up {entity}",
        "can you start {entity}",
        "{entity} on please",
    ),
    "turn_off": (
        "turn off {entity}",
        "switch {entity} off",
        "shut down {entity}",
        "power down {entity}",
        "kill {entity}",
        "{entity} off now",
    ),
    "toggle": (
        "toggle {entity}",
        "flip {entity}",
        "change the state of {entity}",
        "switch {entity} to the other state",
        "toggle that device called {entity}",
    ),
    "get_state": (
        "what is the state of {entity}",
        "check {entity}",
        "is {entity} on",
        "tell me whether {entity} is running",
        "how is {entity} doing",
    ),
    "set_brightness": (
        "set {entity} to {value} percent",
        "make {entity} {value} percent bright",
        "dim {entity} down to {value}",
        "brighten {entity} to {value} percent",
        "put {entity} at {value} percent brightness",
    ),
    "set_color": (
        "make {entity} {color}",
        "set {entity} to {color}",
        "change {entity} color to {color}",
        "I want {entity} glowing {color}",
    ),
    "set_color_temperature": (
        "set {entity} to {value} Kelvin",
        "make {entity} a {value} Kelvin white",
        "change the color temperature of {entity} to {value}",
    ),
    "set_temperature": (
        "set {entity} to {value} degrees",
        "make the thermostat {value}",
        "target {value} degrees on {entity}",
        "raise the target on {entity} to {value}",
    ),
    "set_hvac_mode": (
        "put {entity} in {mode} mode",
        "switch {entity} to {mode}",
        "set the thermostat mode to {mode}",
    ),
    "set_fan_speed": (
        "set {entity} to {value} percent",
        "run {entity} at {value} percent",
        "make {entity} speed {value}",
    ),
    "media_play": (
        "play {entity}",
        "resume {entity}",
        "start playback on {entity}",
        "continue the media on {entity}",
    ),
    "media_pause": (
        "pause {entity}",
        "hold {entity}",
        "pause playback on {entity}",
    ),
    "media_stop": (
        "stop {entity}",
        "stop playback on {entity}",
        "end what is playing on {entity}",
    ),
    "media_next": (
        "play the next track on {entity}",
        "skip this on {entity}",
        "next item on {entity}",
    ),
    "media_previous": (
        "go back one track on {entity}",
        "play the previous item on {entity}",
        "previous on {entity}",
    ),
    "set_volume": (
        "set {entity} volume to {value} percent",
        "make {entity} {value} percent loud",
        "turn {entity} volume down to {value}",
    ),
    "open_cover": (
        "open {entity}",
        "raise {entity}",
        "let the {entity} up",
    ),
    "close_cover": (
        "close {entity}",
        "lower {entity}",
        "bring the {entity} down",
    ),
    "stop_cover": (
        "stop {entity}",
        "hold {entity} where it is",
        "stop the {entity} from moving",
    ),
    "set_cover_position": (
        "set {entity} to {value} percent",
        "open {entity} to {value} percent",
        "put {entity} halfway up at {value}",
    ),
    "lock": (
        "lock {entity}",
        "secure {entity}",
        "please lock the door",
    ),
    "unlock": (
        "unlock {entity}",
        "unsecure {entity}",
        "open the lock on {entity}",
    ),
    "activate_scene": (
        "activate {entity}",
        "set the house to {entity}",
        "start the {entity} scene",
    ),
    "run_script": (
        "run {entity}",
        "start the {entity}",
        "execute my {entity}",
    ),
    "enable_automation": (
        "enable {entity}",
        "turn on {entity}",
        "let {entity} run again",
    ),
    "disable_automation": (
        "disable {entity}",
        "turn off {entity}",
        "stop {entity} from running",
    ),
    "trigger_automation": (
        "trigger {entity}",
        "run {entity} now",
        "start {entity} immediately",
    ),
    "get_temperature": (
        "what is the temperature",
        "how hot is it near {entity}",
        "check {entity}",
        "tell me the current temperature",
    ),
    "get_humidity": (
        "what is the humidity",
        "how humid is it near {entity}",
        "check {entity}",
        "tell me the moisture level",
    ),
    "get_battery": (
        "how much battery does {entity} have",
        "check the battery on {entity}",
        "what charge is left on {entity}",
    ),
    "get_motion": (
        "is there motion at {entity}",
        "check {entity} for movement",
        "did {entity} detect anything",
    ),
}


def _entity_for(action: str, offset: int = 0) -> tuple[str, str]:
    spec = INTENT_CATALOG[action]
    domains = [domain for domain in spec.domains if domain != "*"]
    if action == "get_temperature":
        return _ENTITIES["sensor_temperature"]
    if action == "get_humidity":
        return _ENTITIES["sensor_humidity"]
    if action == "get_battery":
        return _ENTITIES["sensor_battery"]
    if action == "get_motion":
        return _ENTITIES["binary_sensor_motion"]
    if not domains:
        return _ENTITIES["light"]
    domain = domains[offset % len(domains)]
    return _ENTITIES[domain]


def _example(action: str, text: str, entity_id: str, **parameters: Any) -> dict[str, Any]:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return {
        "id": f"seed-{action}-{entity_id.replace('.', '-')}-{digest}",
        "text": text,
        "action": action,
        "entity_id": entity_id,
        "parameters": parameters,
        "source": "catalog_seed",
    }


def generate_seed_examples(examples_per_intent: int = 8) -> list[dict[str, Any]]:
    """Create varied, labeled examples without requiring a language model."""
    if examples_per_intent <= 0:
        raise ValueError("examples_per_intent must be positive")
    values = (20, 40, 55, 70, 85, 100)
    colors = ("red", "blue", "green", "purple", "white")
    modes = ("heat", "cool", "auto", "off")
    examples: list[dict[str, Any]] = []
    for action, templates in _TEMPLATES.items():
        if action not in INTENT_CATALOG:
            continue
        for index in range(examples_per_intent):
            entity_id, entity_name = _entity_for(action, index)
            template = templates[index % len(templates)]
            parameters: dict[str, Any] = {}
            substitutions: dict[str, Any] = {"entity": entity_name}
            if "{value}" in template:
                value = values[index % len(values)]
                substitutions["value"] = value
                if action == "set_brightness":
                    parameters["brightness_pct"] = value
                elif action == "set_color_temperature":
                    parameters["color_temp_kelvin"] = value * 50 + 2000
                    substitutions["value"] = parameters["color_temp_kelvin"]
                elif action == "set_temperature":
                    parameters["temperature"] = value
                    substitutions["value"] = value
                elif action == "set_cover_position":
                    parameters["position"] = value
                elif action == "set_volume":
                    parameters["volume_pct"] = value
                else:
                    parameters["percentage"] = value
            if "{color}" in template:
                substitutions["color"] = colors[index % len(colors)]
                parameters["color"] = substitutions["color"]
            if "{mode}" in template:
                substitutions["mode"] = modes[index % len(modes)]
                parameters["hvac_mode"] = substitutions["mode"]
            text = template.format(**substitutions)
            examples.append(_example(action, text, entity_id, **parameters))
    return examples


def write_seed_dataset(path: str | Path, *, examples_per_intent: int = 8) -> int:
    """Write JSONL training data and return the number of rows written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    examples = generate_seed_examples(examples_per_intent)
    with target.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False, sort_keys=True) + "\n")
    return len(examples)


__all__ = ["generate_seed_examples", "write_seed_dataset"]
