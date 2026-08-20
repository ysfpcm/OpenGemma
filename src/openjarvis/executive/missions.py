"""Bounded Codex mission templates for executive delegation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol


class MissionTemplateName(str, Enum):
    REPOSITORY_INVESTIGATION = "repository-investigation"
    FEATURE_IMPLEMENTATION = "feature-implementation"
    CODE_REVIEW = "code-review"
    REGRESSION_DIAGNOSIS = "regression-diagnosis-repair"
    RD_EXPERIMENT = "rd-experiment"
    WINDOWS_SERVICE_DIAGNOSIS = "windows-service-diagnosis"
    SECURITY_REVIEW = "security-review"


@dataclass(frozen=True)
class CodexMissionTemplate:
    name: MissionTemplateName
    description: str
    mode: str
    success_evidence: tuple[str, ...]
    default_budget: dict[str, int]
    forbidden_effects: tuple[str, ...] = (
        "deploy",
        "publish",
        "send consequential external action",
    )


@dataclass(frozen=True)
class CodexMissionRequest:
    template: MissionTemplateName
    objective: str
    workspace: str
    budgets: dict[str, int]
    context: dict[str, Any]

    def prompt(self) -> str:
        evidence = "; ".join(MissionTemplates.get(self.template).success_evidence)
        forbidden = "; ".join(MissionTemplates.get(self.template).forbidden_effects)
        bounded_context = json.dumps(
            self.context, sort_keys=True, separators=(",", ":")
        )
        return (
            f"Bounded mission: {self.objective}\n"
            f"Success evidence required: {evidence}\n"
            f"Hard boundary: {forbidden}.\n"
            "Bounded specialist context (raw transcript excluded): "
            f"{bounded_context}\n"
            "Return observable findings, changed paths, test evidence, and blockers; "
            "do not include hidden chain-of-thought."
        )


class CodexMissionBoundary(Protocol):
    """The narrow boundary used by the Executive around Phase 1/2 Codex."""

    def start_mission(
        self,
        objective: str,
        workspace: str,
        *,
        mode: str = "read-only",
        budgets: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]: ...

    def resume_observation(self, mission_id: str) -> dict[str, Any]: ...


class MissionTemplates:
    _items = {
        MissionTemplateName.REPOSITORY_INVESTIGATION: CodexMissionTemplate(
            MissionTemplateName.REPOSITORY_INVESTIGATION,
            "Map a repository and identify risks without changing it.",
            "read-only",
            ("scoped files inspected", "risk map", "reproducible findings"),
            {"turns": 4, "tokens": 8000, "minutes": 20},
        ),
        MissionTemplateName.FEATURE_IMPLEMENTATION: CodexMissionTemplate(
            MissionTemplateName.FEATURE_IMPLEMENTATION,
            "Implement one bounded feature in the workspace.",
            "workspace-write",
            ("changed paths", "focused tests", "regression result"),
            {"turns": 8, "tokens": 16000, "minutes": 45},
        ),
        MissionTemplateName.CODE_REVIEW: CodexMissionTemplate(
            MissionTemplateName.CODE_REVIEW,
            "Review a bounded diff for correctness and regressions.",
            "read-only",
            ("findings with paths", "severity", "tests or rationale"),
            {"turns": 4, "tokens": 8000, "minutes": 20},
        ),
        MissionTemplateName.REGRESSION_DIAGNOSIS: CodexMissionTemplate(
            MissionTemplateName.REGRESSION_DIAGNOSIS,
            "Reproduce and repair a bounded regression.",
            "workspace-write",
            ("reproduction", "root cause", "repair test"),
            {"turns": 8, "tokens": 16000, "minutes": 45},
        ),
        MissionTemplateName.RD_EXPERIMENT: CodexMissionTemplate(
            MissionTemplateName.RD_EXPERIMENT,
            "Run an isolated reproducible experiment with deterministic inputs.",
            "workspace-write",
            ("fixture", "measurement", "interpretation", "cleanup state"),
            {"turns": 6, "tokens": 12000, "minutes": 35},
        ),
        MissionTemplateName.WINDOWS_SERVICE_DIAGNOSIS: CodexMissionTemplate(
            MissionTemplateName.WINDOWS_SERVICE_DIAGNOSIS,
            "Diagnose a Windows service without changing service state.",
            "read-only",
            ("service facts", "logs", "safe remediation proposal"),
            {"turns": 5, "tokens": 10000, "minutes": 25},
        ),
        MissionTemplateName.SECURITY_REVIEW: CodexMissionTemplate(
            MissionTemplateName.SECURITY_REVIEW,
            "Review a bounded change for privacy and authority failures.",
            "read-only",
            ("threats", "boundary tests", "remaining limitations"),
            {"turns": 5, "tokens": 10000, "minutes": 25},
        ),
    }

    @classmethod
    def get(cls, name: MissionTemplateName | str) -> CodexMissionTemplate:
        key = MissionTemplateName(name)
        return cls._items[key]

    @classmethod
    def names(cls) -> tuple[MissionTemplateName, ...]:
        return tuple(cls._items)

    @classmethod
    def request(
        cls,
        name: MissionTemplateName | str,
        objective: str,
        workspace: str,
        *,
        budgets: Optional[dict[str, int]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> CodexMissionRequest:
        template = cls.get(name)
        final_budget = dict(template.default_budget)
        for key, value in (budgets or {}).items():
            if key not in final_budget:
                raise ValueError(f"unsupported mission budget: {key}")
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"mission budget {key} must be a non-negative integer")
            final_budget[key] = value
        return CodexMissionRequest(
            template=template.name,
            objective=objective,
            workspace=workspace,
            budgets=final_budget,
            context=dict(context or {}),
        )


__all__ = [
    "CodexMissionBoundary",
    "CodexMissionRequest",
    "CodexMissionTemplate",
    "MissionTemplateName",
    "MissionTemplates",
]
