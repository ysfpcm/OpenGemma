"""Regression evaluation for natural-language behavior examples."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from openjarvis.behavior.models import BehaviorEntity
from openjarvis.behavior.resolver import BehaviorResolver


@dataclass(frozen=True, slots=True)
class BehaviorEvalCase:
    case_id: str
    text: str
    expected_status: str
    expected_action: str | None
    expected_entity_id: str | None
    recent_topic: str | None = None


@dataclass(frozen=True, slots=True)
class BehaviorEvalResult:
    case: BehaviorEvalCase
    passed: bool
    actual_status: str
    actual_action: str | None
    actual_entity_id: str | None
    clarification: str | None = None


@dataclass(frozen=True, slots=True)
class BehaviorEvalReport:
    results: tuple[BehaviorEvalResult, ...]

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0


def entity_from_mapping(value: dict[str, Any]) -> BehaviorEntity:
    return BehaviorEntity(
        entity_id=str(value["entity_id"]),
        name=str(value["name"]),
        domain=str(value.get("domain") or ""),
        area=str(value.get("area") or ""),
        aliases=tuple(str(alias) for alias in value.get("aliases", [])),
    )


def load_cases(path: str | Path) -> list[BehaviorEvalCase]:
    cases: list[BehaviorEvalCase] = []
    for index, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        value = json.loads(line)
        cases.append(
            BehaviorEvalCase(
                case_id=str(value.get("id") or index),
                text=str(value["text"]),
                expected_status=str(value.get("expected_status") or "execute"),
                expected_action=value.get("expected_action"),
                expected_entity_id=value.get("expected_entity_id"),
                recent_topic=value.get("recent_topic"),
            )
        )
    return cases


def evaluate_cases(
    cases: Iterable[BehaviorEvalCase],
    *,
    entities: Sequence[BehaviorEntity],
    resolver: BehaviorResolver,
) -> BehaviorEvalReport:
    results: list[BehaviorEvalResult] = []
    for case in cases:
        resolution = resolver.resolve(case.text, entities=entities, recent_topic=case.recent_topic)
        passed = (
            resolution.status == case.expected_status
            and resolution.action == case.expected_action
            and resolution.entity_id == case.expected_entity_id
        )
        results.append(
            BehaviorEvalResult(
                case=case,
                passed=passed,
                actual_status=resolution.status,
                actual_action=resolution.action,
                actual_entity_id=resolution.entity_id,
                clarification=resolution.clarification,
            )
        )
    return BehaviorEvalReport(tuple(results))


__all__ = [
    "BehaviorEvalCase",
    "BehaviorEvalReport",
    "BehaviorEvalResult",
    "entity_from_mapping",
    "evaluate_cases",
    "load_cases",
]
