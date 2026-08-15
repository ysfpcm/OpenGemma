from __future__ import annotations

from openjarvis.behavior import (
    BehaviorEntity,
    BehaviorResolver,
    BehaviorStore,
    ModelPrediction,
    build_behavior_prompt,
)


ENTITIES = (
    BehaviorEntity(
        "light.livingroomlamp",
        "Living Room Lamp",
        area="Living Room",
        aliases=("reading light", "corner lamp"),
    ),
    BehaviorEntity(
        "light.bedroomlamp",
        "Bedroom Lamp",
        area="Bedroom",
        aliases=("nightstand light", "bedside light"),
    ),
    BehaviorEntity(
        "light.kitchenlight",
        "Kitchen Light",
        area="Kitchen",
        aliases=("counter light", "kitchen lights"),
    ),
)


def test_explicit_entity_returns_canonical_tool_call() -> None:
    resolution = BehaviorResolver().resolve(
        "turn it on",
        entities=ENTITIES,
        recent_topic="Living Room Lamp",
    )

    assert resolution.should_execute
    assert resolution.tool_call() == {
        "action": "turn_on",
        "entity": "light.livingroomlamp",
    }
    assert resolution.confidence >= 0.8


def test_ambiguous_generic_device_asks_one_short_question() -> None:
    resolution = BehaviorResolver().resolve("turn on the lamp", entities=ENTITIES)

    assert resolution.status == "clarify"
    assert resolution.action == "turn_on"
    assert resolution.entity_id is None
    assert resolution.clarification == "Which device do you mean: Living Room Lamp or Bedroom Lamp?"


def test_model_prediction_can_supply_action_but_not_invent_entity() -> None:
    resolution = BehaviorResolver().resolve(
        "make it happen",
        entities=ENTITIES,
        recent_topic="light.kitchenlight",
        model_prediction=ModelPrediction(action="turn_on", confidence=0.91),
    )

    assert resolution.status == "clarify"
    assert resolution.action == "turn_on"
    assert "Which device" in (resolution.clarification or "")


def test_correction_is_saved_and_reused_only_with_matching_topic(tmp_path) -> None:
    with BehaviorStore(tmp_path / "behavior.db") as store:
        resolver = BehaviorResolver(store)
        example = resolver.teach(
            before_text="make the cozy one glow",
            corrected_action="turn_on",
            corrected_entity="light.livingroomlamp",
            recent_topic="light.livingroomlamp",
        )
        assert example.id == 1
        assert store.count() == 1

        learned = resolver.resolve(
            "make the cozy one glow",
            entities=ENTITIES,
            recent_topic="light.livingroomlamp",
        )
        unrelated = resolver.resolve(
            "make the cozy one glow",
            entities=ENTITIES,
            recent_topic="light.bedroomlamp",
        )

    assert learned.tool_call() == {
        "action": "turn_on",
        "entity": "light.livingroomlamp",
    }
    assert unrelated.status == "unsupported"


def test_prompt_contract_mentions_ambiguity_and_canonical_ids() -> None:
    prompt = build_behavior_prompt(ENTITIES, recent_topic=ENTITIES[0])

    assert "entity_id" in prompt
    assert "never invent one" in prompt
    assert "light.livingroomlamp" in prompt
