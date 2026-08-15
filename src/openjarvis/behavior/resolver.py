"""Context-aware, conservative resolution of natural Home Assistant speech."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from openjarvis.behavior.models import (
    BehaviorEntity,
    BehaviorResolution,
    ModelPrediction,
    SUPPORTED_ACTIONS,
)
from openjarvis.behavior.catalog import get_intent_spec
from openjarvis.behavior.normalization import (
    is_reference_phrase,
    normalize_text,
    text_similarity,
    token_similarity,
    tokenize,
)
from openjarvis.behavior.store import BehaviorStore, CorrectionMatch

_REFERENCE_WORDS = {
    "it",
    "this",
    "that",
    "one",
    "light",
    "lamp",
    "device",
    "fan",
    "thermostat",
    "tv",
    "speaker",
    "blinds",
    "shade",
    "curtain",
    "door",
    "lock",
    "plug",
    "switch",
}


@dataclass(frozen=True, slots=True)
class _EntityMatch:
    entity: BehaviorEntity
    score: float
    alias: str


class BehaviorResolver:
    """Resolve a transcript into a canonical action/entity command.

    The resolver is intentionally more cautious as ambiguity increases. It
    may return a clarification question, but it never executes a device call.
    The caller should pass ``resolution.tool_call()`` to the verified
    HomeAssistantTool and report that tool's result to the user.
    """

    def __init__(self, store: BehaviorStore | None = None) -> None:
        self.store = store

    def resolve(
        self,
        utterance: str,
        *,
        entities: Sequence[BehaviorEntity],
        recent_topic: BehaviorEntity | str | None = None,
        model_prediction: ModelPrediction | dict[str, Any] | None = None,
    ) -> BehaviorResolution:
        normalized = normalize_text(utterance)
        if not normalized:
            return self._clarify(utterance, normalized, "What would you like me to do?")

        prediction = (
            model_prediction
            if isinstance(model_prediction, ModelPrediction)
            else ModelPrediction.from_mapping(model_prediction)
        )
        enabled_entities = tuple(entity for entity in entities if entity.enabled)
        topic_entity = self._resolve_topic(recent_topic, enabled_entities)
        correction = self._best_correction(normalized, topic_entity)

        action, action_confidence, action_source, parameters = self._resolve_action(
            normalized, prediction, correction
        )
        if action is None:
            return BehaviorResolution(
                status="unsupported",
                utterance=utterance,
                normalized_utterance=normalized,
                confidence=0.0,
                source="none",
                clarification=(
                    "I can control supported devices, but I did not understand "
                    "the action."
                ),
                evidence=("no supported action detected",),
            )

        spec = get_intent_spec(action)
        if spec is None:
            return BehaviorResolution(
                status="unsupported",
                utterance=utterance,
                normalized_utterance=normalized,
                parameters=parameters,
                confidence=0.0,
                source=action_source,
                clarification=f"I do not support the {action} action yet.",
            )
        missing_parameters = [
            name for name in spec.required_parameters if name not in parameters
        ]
        if missing_parameters:
            return self._clarify(
                utterance,
                normalized,
                f"What value should I use for {missing_parameters[0].replace('_', ' ')}?",
                action=action,
                source=action_source,
                confidence=min(action_confidence, 0.49),
                parameters=parameters,
                correction_id=correction.example.id if correction else None,
            )

        entity_match, clarification = self._resolve_entity(
            normalized,
            enabled_entities,
            topic_entity=topic_entity,
            prediction=prediction,
            correction=correction,
            action=action,
        )
        if clarification:
            return self._clarify(
                utterance,
                normalized,
                clarification,
                action=action,
                source=action_source,
                parameters=parameters,
                confidence=min(action_confidence, 0.49),
                correction_id=correction.example.id if correction else None,
            )

        available_domains = {entity.domain for entity in enabled_entities}
        domain_available = "*" in spec.domains or bool(
            available_domains.intersection(spec.domains)
        )
        if entity_match is None and not domain_available and action != "get_temperature":
            return BehaviorResolution(
                status="unsupported",
                utterance=utterance,
                normalized_utterance=normalized,
                parameters=parameters,
                confidence=round(action_confidence, 3),
                source=action_source,
                clarification=(
                    f"I understood {action.replace('_', ' ')}, but no matching "
                    "Home Assistant device is available in the current context."
                ),
                correction_id=correction.example.id if correction else None,
            )

        if entity_match is None and action != "get_temperature":
            return self._clarify(
                utterance,
                normalized,
                "Which device should I use?",
                action=action,
                source=action_source,
                parameters=parameters,
                confidence=min(action_confidence, 0.49),
                correction_id=correction.example.id if correction else None,
            )

        entity_confidence = entity_match.score if entity_match else 1.0
        confidence = min(action_confidence, entity_confidence)
        evidence = [f"action={action}"]
        if entity_match:
            evidence.append(f"entity alias={entity_match.alias!r}")
        if topic_entity and is_reference_phrase(normalized):
            evidence.append(f"recent topic={topic_entity.entity_id}")
        if correction:
            evidence.append(f"learned example={correction.example.id}")
        return BehaviorResolution(
            status="execute",
            utterance=utterance,
            normalized_utterance=normalized,
            action=action,
            entity_id=entity_match.entity.entity_id if entity_match else None,
            entity_name=entity_match.entity.name if entity_match else None,
            parameters=parameters,
            confidence=round(confidence, 3),
            source=action_source,
            evidence=tuple(evidence),
            correction_id=correction.example.id if correction else None,
        )

    def teach(
        self,
        *,
        before_text: str,
        corrected_action: str,
        corrected_entity: BehaviorEntity | str | None = None,
        recent_topic: BehaviorEntity | str | None = None,
        predicted: ModelPrediction | dict[str, Any] | None = None,
        corrected_parameters: Mapping[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ):
        """Persist one correction so future matching can use it."""
        if self.store is None:
            raise RuntimeError("BehaviorResolver.teach requires a BehaviorStore")
        prediction = (
            predicted
            if isinstance(predicted, ModelPrediction)
            else ModelPrediction.from_mapping(predicted)
        )
        corrected_id, corrected_name = self._entity_identity(corrected_entity)
        topic_id, topic_name = self._entity_identity(recent_topic)
        return self.store.record_correction(
            before_text=before_text,
            corrected_action=corrected_action,
            corrected_entity_id=corrected_id,
            corrected_entity_name=corrected_name,
            recent_topic_entity_id=topic_id or topic_name,
            predicted_action=prediction.action,
            predicted_entity_id=prediction.entity_id,
            corrected_parameters=corrected_parameters,
            predicted_parameters=prediction.parameters,
            context=context,
        )

    @staticmethod
    def _entity_identity(value: BehaviorEntity | str | None) -> tuple[str | None, str | None]:
        if isinstance(value, BehaviorEntity):
            return value.entity_id, value.name
        text = str(value or "").strip()
        if not text:
            return None, None
        return (text, None) if "." in text else (None, text)

    def _best_correction(
        self,
        normalized: str,
        topic_entity: BehaviorEntity | None,
    ) -> CorrectionMatch | None:
        if self.store is None:
            return None
        topic_id = topic_entity.entity_id if topic_entity else None
        for match in self.store.find_matches(normalized, recent_topic_entity_id=topic_id):
            example = match.example
            if match.similarity < 0.76:
                break
            if example.recent_topic_entity_id:
                stored_topic = normalize_text(example.recent_topic_entity_id)
                topic_names = {
                    normalize_text(topic_id),
                    normalize_text(topic_entity.name if topic_entity else ""),
                }
                if topic_id and stored_topic not in topic_names:
                    continue
                if not topic_id and is_reference_phrase(normalized):
                    continue
            return match
        return None

    def _resolve_action(
        self,
        normalized: str,
        prediction: ModelPrediction,
        correction: CorrectionMatch | None,
    ) -> tuple[str | None, float, str, dict[str, Any]]:
        inferred, confidence, inferred_parameters = self._action_from_text(normalized)
        if correction and correction.similarity >= 0.95:
            corrected = correction.example.corrected_action
            if inferred is None or inferred == corrected:
                return (
                    corrected,
                    0.99,
                    "learned_correction",
                    dict(correction.example.corrected_parameters),
                )
        if inferred:
            return inferred, confidence, "speech_pattern", inferred_parameters
        if prediction.action in SUPPORTED_ACTIONS:
            return (
                prediction.action,
                min(max(prediction.confidence, 0.55), 0.9),
                "model_prediction",
                dict(prediction.parameters),
            )
        if correction:
            return (
                correction.example.corrected_action,
                0.82,
                "learned_correction",
                dict(correction.example.corrected_parameters),
            )
        return None, 0.0, "none", {}

    @staticmethod
    def _action_from_text(
        normalized: str,
    ) -> tuple[str | None, float, dict[str, Any]]:
        tokens = tokenize(normalized)
        token_set = set(tokens)

        def result(
            action: str,
            confidence: float,
            parameters: Mapping[str, Any] | None = None,
        ) -> tuple[str, float, dict[str, Any]]:
            return action, confidence, dict(parameters or {})

        number_match = re.search(r"(-?\d+(?:\.\d+)?)", normalized)
        number = float(number_match.group(1)) if number_match else None
        if number is not None and number.is_integer():
            number = int(number)

        if re.search(r"\b(?:temperature|temp|hot|cold)\b", normalized):
            if number is not None and re.search(
                r"\b(?:set|make|raise|lower|target|thermostat|climate)\b",
                normalized,
            ):
                return result("set_temperature", 0.9, {"temperature": number})
            return result("get_temperature", 0.9)
        if "humidity" in token_set or "humid" in token_set:
            return result("get_humidity", 0.9)
        if "battery" in token_set or "charge" in token_set:
            return result("get_battery", 0.86)
        if "motion" in token_set or "occupancy" in token_set:
            return result("get_motion", 0.86)

        if number is not None and any(
            word in token_set for word in ("thermostat", "climate", "heat", "cool")
        ):
            return result("set_temperature", 0.9, {"temperature": number})
        if any(word in token_set for word in ("heat", "cool", "auto")) and any(
            word in token_set for word in ("thermostat", "climate", "hvac", "mode")
        ):
            mode = next(word for word in ("heat", "cool", "auto") if word in token_set)
            return result("set_hvac_mode", 0.87, {"hvac_mode": mode})

        if any(word in token_set for word in ("brightness", "dim", "dimmer", "brighter")) or (
            number is not None
            and "percent" in token_set
            and any(word in token_set for word in ("light", "lamp", "bulb"))
        ):
            return result(
                "set_brightness",
                0.84,
                {"brightness_pct": number} if number is not None else {},
            )
        colors = {"red", "blue", "green", "purple", "white", "orange", "yellow", "pink"}
        color = next((word for word in colors if word in token_set), None)
        if color and any(word in token_set for word in ("light", "lamp", "bulb")):
            return result("set_color", 0.84, {"color": color})
        if "kelvin" in token_set or "color temperature" in normalized:
            return result(
                "set_color_temperature",
                0.84,
                {"color_temp_kelvin": number} if number is not None else {},
            )

        if "fan" in token_set and (
            "speed" in token_set
            or "percentage" in token_set
            or "percent" in token_set
            or "%" in normalized
        ):
            return result(
                "set_fan_speed",
                0.86,
                {"percentage": number} if number is not None else {},
            )
        if "volume" in token_set and number is not None:
            return result("set_volume", 0.86, {"volume_pct": number})

        if any(word in token_set for word in ("blind", "blinds", "shade", "shades", "curtain", "curtains", "cover")):
            if number is not None or "halfway" in token_set or "quarter" in token_set:
                position = number
                if position is None and "halfway" in token_set:
                    position = 50
                if position is None and "quarter" in token_set:
                    position = 25
                return result("set_cover_position", 0.86, {"position": position})
            if "open" in token_set or "raise" in token_set:
                return result("open_cover", 0.88)
            if "close" in token_set or "lower" in token_set:
                return result("close_cover", 0.88)
            if "stop" in token_set or "hold" in token_set:
                return result("stop_cover", 0.84)

        if "unlock" in token_set or "unsecure" in token_set:
            return result("unlock", 0.9)
        if "lock" in token_set or "secure" in token_set:
            return result("lock", 0.9)

        if "scene" in token_set or "mode" in token_set:
            if any(word in token_set for word in ("activate", "set", "start", "run")):
                return result("activate_scene", 0.82)
        if any(word in token_set for word in ("script", "routine")) and any(
            word in token_set for word in ("run", "start", "execute")
        ):
            return result("run_script", 0.84)
        if "automation" in token_set:
            if "trigger" in token_set or "run" in token_set:
                return result("trigger_automation", 0.84)
            if "enable" in token_set or "turn on" in normalized:
                return result("enable_automation", 0.84)
            if "disable" in token_set or "turn off" in normalized:
                return result("disable_automation", 0.84)

        media_words = {"tv", "music", "song", "track", "speaker", "media", "player"}
        if token_set & media_words:
            if "pause" in token_set or "hold" in token_set:
                return result("media_pause", 0.86)
            if "stop" in token_set:
                return result("media_stop", 0.86)
            if "next" in token_set or "skip" in token_set:
                return result("media_next", 0.84)
            if "previous" in token_set or "back" in token_set:
                return result("media_previous", 0.84)
            if "play" in token_set or "resume" in token_set:
                return result("media_play", 0.86)

        if re.search(r"\b(?:status|state|condition|check|available)\b", normalized):
            return result("get_state", 0.9)
        if re.search(
            r"\b(?:is|are|if|whether)\b.*\b(?:on|off|unavailable|available)\b",
            normalized,
        ):
            return result("get_state", 0.86)

        has_turn = any(token_similarity(token, "turn") >= 0.74 for token in tokens)
        has_switch = any(token_similarity(token, "switch") >= 0.78 for token in tokens)
        has_power = any(token_similarity(token, "power") >= 0.78 for token in tokens)
        has_put = "put" in token_set
        has_wake = "wake" in token_set or "illuminate" in token_set
        has_shut = "shut" in token_set or "kill" in token_set
        has_glow = "glow" in token_set or "shine" in token_set
        has_up = "up" in token_set
        has_on = "on" in token_set
        has_off = "off" in token_set or "out" in token_set or any(
            token_similarity(token, "off") >= 0.78 for token in tokens
        )

        if (has_on or has_up or has_wake or has_glow) and (
            has_turn
            or has_switch
            or has_power
            or has_put
            or has_wake
            or has_glow
            or "lights" in token_set
            or "light" in token_set
        ):
            return result("turn_on", 0.95 if has_turn or has_switch else 0.88)
        if (has_off or has_shut) and (
            has_turn
            or has_switch
            or has_power
            or has_shut
            or "lights" in token_set
            or "light" in token_set
            or bool(token_set & _REFERENCE_WORDS)
        ):
            return result("turn_off", 0.95 if has_turn or has_switch else 0.88)
        if "toggle" in token_set or ("flip" in token_set and "on" not in token_set):
            return result("toggle", 0.82)
        if "on" in token_set and any(word in token_set for word in _REFERENCE_WORDS):
            return result("turn_on", 0.82)
        if "off" in token_set and any(word in token_set for word in _REFERENCE_WORDS):
            return result("turn_off", 0.82)
        return None, 0.0, {}

    def _resolve_entity(
        self,
        normalized: str,
        entities: Sequence[BehaviorEntity],
        *,
        topic_entity: BehaviorEntity | None,
        prediction: ModelPrediction,
        correction: CorrectionMatch | None,
        action: str,
    ) -> tuple[_EntityMatch | None, str | None]:
        spec = get_intent_spec(action)
        if spec is not None and "*" not in spec.domains:
            entities = tuple(entity for entity in entities if entity.domain in spec.domains)
        if action == "get_temperature" and not self._rank_entities(normalized, entities):
            return None, None

        if correction and (
            correction.example.corrected_entity_id
            or correction.example.corrected_entity_name
        ):
            corrected_id = correction.example.corrected_entity_id
            corrected_name = normalize_text(correction.example.corrected_entity_name)
            corrected = next(
                (
                    entity
                    for entity in entities
                    if entity.entity_id == corrected_id
                    or (
                        corrected_name
                        and normalize_text(entity.name) == corrected_name
                    )
                ),
                None,
            )
            if corrected:
                return _EntityMatch(corrected, 0.99, "learned correction"), None

        if prediction.entity_id:
            predicted = next(
                (entity for entity in entities if entity.entity_id == prediction.entity_id),
                None,
            )
            if predicted:
                return (
                    _EntityMatch(predicted, max(prediction.confidence, 0.8), "model entity_id"),
                    None,
                )

        matches = self._rank_entities(normalized, entities)
        if matches:
            top = matches[0]
            second = matches[1] if len(matches) > 1 else None
            if second and top.score < 0.98 and top.score - second.score < 0.12:
                return None, self._ambiguity_question(matches[:3])
            if top.score >= 0.76:
                return top, None

        if prediction.entity_mention:
            model_matches = self._rank_entities(normalize_text(prediction.entity_mention), entities)
            if model_matches and model_matches[0].score >= 0.76:
                top = model_matches[0]
                if len(model_matches) > 1 and top.score - model_matches[1].score < 0.12:
                    return None, self._ambiguity_question(model_matches[:3])
                return (
                    _EntityMatch(
                        top.entity,
                        max(top.score, prediction.confidence),
                        "model entity mention",
                    ),
                    None,
                )

        if is_reference_phrase(normalized) and topic_entity:
            return _EntityMatch(topic_entity, 0.9, "recent topic"), None

        if is_reference_phrase(normalized):
            domain_hint = (
                "light"
                if any(word in normalized.split() for word in ("light", "lamp", "lights"))
                else ""
            )
            candidates = [
                entity for entity in entities if not domain_hint or entity.domain == "light"
            ]
            if len(candidates) == 1:
                return _EntityMatch(candidates[0], 0.76, "only matching device"), None
            if len(candidates) > 1:
                return None, self._ambiguity_question(
                    [_EntityMatch(entity, 0.7, "device type") for entity in candidates[:3]]
                )
        return None, None

    @staticmethod
    def _rank_entities(normalized: str, entities: Sequence[BehaviorEntity]) -> list[_EntityMatch]:
        utterance_tokens = tokenize(normalized)
        utterance_set = set(utterance_tokens)
        matches: list[_EntityMatch] = []
        for entity in entities:
            aliases = {
                entity.name,
                entity.entity_id,
                entity.entity_id.split(".", 1)[-1].replace("_", " "),
                *entity.aliases,
            }
            if entity.area:
                aliases.add(entity.area)
                aliases.add(f"{entity.area} {entity.name}")
                if entity.domain == "light":
                    if "lamp" in normalize_text(entity.name).split():
                        aliases.add(f"{entity.area} light")
                    if "light" in normalize_text(entity.name).split():
                        aliases.add(f"{entity.area} lamp")
            best_score = 0.0
            best_alias = ""
            for raw_alias in aliases:
                alias = normalize_text(raw_alias)
                alias_tokens = tokenize(alias)
                if not alias_tokens:
                    continue
                if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
                    score = 1.0 if len(alias_tokens) > 1 else 0.84
                elif set(alias_tokens) <= utterance_set:
                    score = 0.95 if len(alias_tokens) > 1 else 0.84
                else:
                    score = BehaviorResolver._fuzzy_alias_score(alias_tokens, utterance_tokens)
                if score > best_score:
                    best_score, best_alias = score, raw_alias
            if best_score:
                matches.append(_EntityMatch(entity, best_score, best_alias))
        return sorted(
            matches,
            key=lambda item: (item.score, len(tokenize(item.alias))),
            reverse=True,
        )

    @staticmethod
    def _fuzzy_alias_score(alias_tokens: Sequence[str], utterance_tokens: Sequence[str]) -> float:
        if len(alias_tokens) > len(utterance_tokens) or len(alias_tokens) == 0:
            return 0.0
        best = 0.0
        for index in range(len(utterance_tokens) - len(alias_tokens) + 1):
            window = utterance_tokens[index : index + len(alias_tokens)]
            average = sum(
                token_similarity(left, right) for left, right in zip(alias_tokens, window)
            ) / len(alias_tokens)
            if average >= 0.78 and all(
                token_similarity(left, right) >= 0.84
                for left, right in zip(alias_tokens, window)
            ):
                best = max(best, 0.76 + 0.2 * average)
        return best

    @staticmethod
    def _resolve_topic(
        recent_topic: BehaviorEntity | str | None,
        entities: Sequence[BehaviorEntity],
    ) -> BehaviorEntity | None:
        if isinstance(recent_topic, BehaviorEntity):
            return next(
                (
                    entity
                    for entity in entities
                    if entity.entity_id == recent_topic.entity_id
                ),
                recent_topic,
            )
        topic = normalize_text(recent_topic)
        if not topic:
            return None
        matches = BehaviorResolver._rank_entities(topic, entities)
        return matches[0].entity if matches and matches[0].score >= 0.76 else None

    @staticmethod
    def _ambiguity_question(matches: Iterable[_EntityMatch]) -> str:
        names = []
        for match in matches:
            if match.entity.name not in names:
                names.append(match.entity.name)
        if not names:
            return "Which device do you mean?"
        return f"Which device do you mean: {' or '.join(names[:3])}?"

    @staticmethod
    def _clarify(
        utterance: str,
        normalized: str,
        clarification: str,
        *,
        action: str | None = None,
        source: str = "",
        confidence: float = 0.0,
        parameters: Mapping[str, Any] | None = None,
        correction_id: int | None = None,
    ) -> BehaviorResolution:
        return BehaviorResolution(
            status="clarify",
            utterance=utterance,
            normalized_utterance=normalized,
            action=action,
            parameters=dict(parameters or {}),
            confidence=round(confidence, 3),
            source=source,
            clarification=clarification,
            correction_id=correction_id,
        )


__all__ = ["BehaviorResolver"]
