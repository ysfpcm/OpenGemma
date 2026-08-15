from __future__ import annotations

import json
from pathlib import Path

from openjarvis.behavior import (
    BehaviorResolver,
    BehaviorStore,
    entity_from_mapping,
    evaluate_cases,
    load_cases,
)


ROOT = Path(__file__).resolve().parents[2]


def test_natural_language_regression_set_is_green() -> None:
    entity_values = json.loads((ROOT / "examples/behavior/ophanim_entities.json").read_text())
    entities = tuple(entity_from_mapping(value) for value in entity_values)
    cases = load_cases(ROOT / "examples/behavior/ophanim_eval.jsonl")

    with BehaviorStore(":memory:") as store:
        report = evaluate_cases(cases, entities=entities, resolver=BehaviorResolver(store))

    assert report.total == 60
    assert report.passed == 60
