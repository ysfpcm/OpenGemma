"""Specialist roles and minimum-sufficient context packaging."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import ExecutiveGoal, GlobalWorkspace, SpecialistClaim, SpecialistRole


@dataclass(frozen=True)
class SpecialistSpec:
    role: SpecialistRole
    purpose: str
    allowed_capabilities: tuple[str, ...]
    default_budget: dict[str, int]


@dataclass(frozen=True)
class BoundedContext:
    """Context sent to one specialist; transcripts and unrelated claims are absent."""

    goal_id: str
    assignment_id: str
    role: str
    objective: str
    goal_summary: str
    success_conditions: tuple[str, ...]
    current_focus: str
    relevant_observations: tuple[dict[str, Any], ...] = ()
    beliefs: tuple[dict[str, Any], ...] = ()
    constraints: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    relevant_claims: tuple[dict[str, Any], ...] = ()
    guardian_feedback: tuple[dict[str, Any], ...] = ()
    budget: dict[str, int] = field(default_factory=dict)
    allowed_capabilities: tuple[str, ...] = ()
    transcript_included: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "assignment_id": self.assignment_id,
            "role": self.role,
            "objective": self.objective,
            "goal_summary": self.goal_summary,
            "success_conditions": list(self.success_conditions),
            "current_focus": self.current_focus,
            "relevant_observations": list(self.relevant_observations),
            "beliefs": list(self.beliefs),
            "constraints": list(self.constraints),
            "unresolved_questions": list(self.unresolved_questions),
            "relevant_claims": list(self.relevant_claims),
            "guardian_feedback": list(self.guardian_feedback),
            "budget": dict(self.budget),
            "allowed_capabilities": list(self.allowed_capabilities),
            "transcript_included": self.transcript_included,
        }


def specialist_specs() -> dict[str, SpecialistSpec]:
    """Return the initial typed council vocabulary."""
    return {
        SpecialistRole.PERSONAL_CONTEXT.value: SpecialistSpec(
            SpecialistRole.PERSONAL_CONTEXT,
            "Find relevant durable personal context and memory evidence.",
            ("read-personal-context",),
            {"turns": 2, "tokens": 2500, "minutes": 5},
        ),
        SpecialistRole.PLANNING.value: SpecialistSpec(
            SpecialistRole.PLANNING,
            "Sequence bounded work, dependencies, deadlines, and pause points.",
            ("propose-plan",),
            {"turns": 2, "tokens": 2500, "minutes": 5},
        ),
        SpecialistRole.SOFTWARE.value: SpecialistSpec(
            SpecialistRole.SOFTWARE,
            "Investigate and implement bounded software changes through Codex.",
            ("codex-read", "codex-workspace-write"),
            {"turns": 5, "tokens": 9000, "minutes": 30},
        ),
        SpecialistRole.HOME_SYSTEMS.value: SpecialistSpec(
            SpecialistRole.HOME_SYSTEMS,
            "Review home-system dependencies without issuing effects.",
            ("read-home-state",),
            {"turns": 2, "tokens": 2000, "minutes": 5},
        ),
        SpecialistRole.SECURITY.value: SpecialistSpec(
            SpecialistRole.SECURITY,
            "Review privacy, authority, injection, and failure boundaries.",
            ("security-review",),
            {"turns": 3, "tokens": 4000, "minutes": 10},
        ),
        SpecialistRole.EVIDENCE_CRITIC.value: SpecialistSpec(
            SpecialistRole.EVIDENCE_CRITIC,
            "Test whether claims have reproducible, sufficient evidence.",
            ("inspect-evidence",),
            {"turns": 3, "tokens": 3500, "minutes": 10},
        ),
        SpecialistRole.PLAN_CRITIC.value: SpecialistSpec(
            SpecialistRole.PLAN_CRITIC,
            "Find plan gaps, disagreement, and seeded failure modes.",
            ("failure-analysis",),
            {"turns": 3, "tokens": 3500, "minutes": 10},
        ),
        SpecialistRole.COMMUNICATION.value: SpecialistSpec(
            SpecialistRole.COMMUNICATION,
            "Shape one concise, truthful Ophanim narrative for Marc.",
            ("synthesize-report",),
            {"turns": 2, "tokens": 2500, "minutes": 5},
        ),
    }


class SpecialistRouter:
    """Builds bounded packages and never passes a raw transcript."""

    def __init__(self, specs: dict[str, SpecialistSpec] | None = None) -> None:
        self.specs = specs or specialist_specs()

    def package(
        self,
        goal: ExecutiveGoal,
        workspace: GlobalWorkspace,
        *,
        assignment_id: str,
        role: str,
        objective: str,
        claims: Iterable[SpecialistClaim] = (),
        budget: dict[str, int] | None = None,
    ) -> BoundedContext:
        spec = self.specs.get(role)
        if spec is None:
            raise ValueError(f"unknown specialist role: {role}")
        relevant_claims = tuple(
            {
                "id": claim.id,
                "statement": claim.statement,
                "confidence": claim.confidence,
                "evidence_ids": list(claim.evidence_ids),
                "artifact_ids": list(claim.artifact_ids),
                "status": claim.status.value,
            }
            for claim in claims
            if claim.goal_id == goal.id
        )
        # Keep the package bounded even when a long-running mission accumulates
        # many observations or claims.  The durable store retains the full
        # evidence; specialists receive only the minimum sufficient slice.
        return BoundedContext(
            goal_id=goal.id,
            assignment_id=assignment_id,
            role=role,
            objective=objective,
            goal_summary=goal.objective,
            success_conditions=tuple(goal.success_conditions),
            current_focus=workspace.current_focus,
            relevant_observations=tuple(workspace.relevant_observations[-8:]),
            beliefs=tuple(workspace.beliefs[-8:]),
            constraints=tuple(workspace.constraints),
            unresolved_questions=tuple(workspace.unresolved_questions),
            relevant_claims=relevant_claims[-8:],
            guardian_feedback=tuple(workspace.guardian_feedback[-8:]),
            budget=dict(budget or spec.default_budget),
            allowed_capabilities=spec.allowed_capabilities,
        )


__all__ = ["BoundedContext", "SpecialistRouter", "SpecialistSpec", "specialist_specs"]
