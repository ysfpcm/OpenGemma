"""Deterministic typed planner for Phase 6."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from openjarvis.guardian import ActionRegistry

from .models import (
    Condition,
    PlanningContext,
    PlanStatus,
    PlanStep,
    PlanValidationError,
    StructuredPlan,
    ValidationReport,
)
from .store import PlanningStore


def _parse(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return timestamp.astimezone(timezone.utc)


class TypedPlanner:
    """Build and validate plans without any execution capability."""

    def __init__(self, registry: ActionRegistry, store: PlanningStore) -> None:
        self.registry = registry
        self.store = store

    def validate(
        self, plan: StructuredPlan, context: PlanningContext
    ) -> ValidationReport:
        reasons: list[str] = []
        if plan.status is not PlanStatus.ACTIVE:
            reasons.append(f"plan is {plan.status.value}")
        if _parse(plan.valid_until) <= context.now_datetime:
            reasons.append("plan is expired")
        if plan.situation_id != context.situation_id:
            reasons.append("situation identity changed")
        if plan.situation_type != context.situation_type:
            reasons.append("situation type changed")
        snapshot = dict(plan.context_snapshot)
        for key, expected in context.identity().items():
            if snapshot.get(key) != expected:
                reasons.append(f"context changed: {key}")
        if sum(step.resource_cost for step in plan.steps) > plan.resource_budget:
            reasons.append("plan resource budget exceeded")

        step_ids = {step.step_id for step in plan.steps}
        if len(step_ids) != len(plan.steps):
            reasons.append("plan contains duplicate step IDs")
        if set(plan.removed_step_ids) & step_ids:
            reasons.append("removed action is present in the plan")

        graph = {step.step_id: set(step.dependencies) for step in plan.steps}
        for step_id, dependencies in graph.items():
            missing = dependencies - step_ids
            if missing:
                reasons.append(
                    f"step {step_id} has missing dependencies: {sorted(missing)}"
                )
            if step_id in dependencies:
                reasons.append(f"step {step_id} depends on itself")
        if self._has_cycle(graph):
            reasons.append("plan dependencies contain a cycle")

        for step in plan.steps:
            self._validate_step(step, plan, context, reasons)
        return ValidationReport(not reasons, tuple(dict.fromkeys(reasons)))

    def _validate_step(
        self,
        step: PlanStep,
        plan: StructuredPlan,
        context: PlanningContext,
        reasons: list[str],
    ) -> None:
        try:
            definition = self.registry.get(step.proposal.action_type)
        except KeyError:
            reasons.append(f"step {step.step_id} uses an unregistered action")
            return
        schema_error = self.registry.validate(step.proposal)
        if schema_error:
            reasons.append(f"step {step.step_id}: {schema_error}")
        if step.executor != step.proposal.action_type:
            reasons.append(f"step {step.step_id} names an unregistered executor")
        if step.capability != definition.capability:
            reasons.append(f"step {step.step_id} capability does not match registry")
        if step.consequence_class != definition.consequence_class:
            reasons.append(
                f"step {step.step_id} consequence class does not match registry"
            )
        if step.reversible and definition.compensation is None:
            reasons.append(
                f"step {step.step_id} claims reversibility without compensation"
            )
        if not callable(definition.executor) or not callable(definition.verifier):
            reasons.append(f"step {step.step_id} has an invalid registered executor")
        if _parse(step.expires_at) > _parse(plan.valid_until):
            reasons.append(f"step {step.step_id} outlives the plan")
        for condition in step.preconditions:
            self._validate_condition(step, condition, context, reasons)
        for condition in step.cancellation_conditions:
            self._validate_cancellation(step, condition, context, reasons)
        for expected in step.expected_observations:
            if expected.observation_key not in context.observations:
                reasons.append(
                    f"step {step.step_id} is missing expected observation "
                    f"{expected.observation_key}"
                )

    @staticmethod
    def _validate_condition(
        step: PlanStep,
        condition: Condition,
        context: PlanningContext,
        reasons: list[str],
    ) -> None:
        observation = context.observations.get(condition.observation_key)
        if observation is None:
            reasons.append(
                f"step {step.step_id} is missing observation "
                f"{condition.observation_key}"
            )
            return
        if observation.satisfied != condition.expected:
            reasons.append(f"step {step.step_id} precondition failed: {condition.name}")
        if observation.age_seconds(context.now_datetime) > condition.max_age_seconds:
            reasons.append(
                f"step {step.step_id} has stale observation: {condition.name}"
            )
        if observation.confidence < condition.min_confidence:
            reasons.append(
                f"step {step.step_id} has low-confidence observation: {condition.name}"
            )

    @staticmethod
    def _validate_cancellation(
        step: PlanStep,
        condition: Condition,
        context: PlanningContext,
        reasons: list[str],
    ) -> None:
        observation = context.observations.get(condition.observation_key)
        if observation is None:
            reasons.append(
                f"step {step.step_id} is missing cancellation observation "
                f"{condition.observation_key}"
            )
            return
        if observation.age_seconds(context.now_datetime) > condition.max_age_seconds:
            reasons.append(
                f"step {step.step_id} has stale cancellation observation: "
                f"{condition.name}"
            )
        if observation.confidence < condition.min_confidence:
            reasons.append(
                f"step {step.step_id} has low-confidence cancellation observation: "
                f"{condition.name}"
            )
        if observation.satisfied == condition.expected:
            reasons.append(
                f"step {step.step_id} cancellation condition met: {condition.name}"
            )

    @staticmethod
    def _has_cycle(graph: dict[str, set[str]]) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            if any(visit(child) for child in graph.get(node, ())):
                return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in graph)

    def generate(
        self,
        *,
        plan_id: str,
        goal_id: str,
        context: PlanningContext,
        valid_until: str,
        rationale: str,
        evidence_ids: Iterable[str],
        steps: Iterable[PlanStep],
        resource_budget: float,
    ) -> StructuredPlan:
        plan = StructuredPlan(
            plan_id=plan_id,
            version=1,
            goal_id=goal_id,
            situation_id=context.situation_id,
            situation_type=context.situation_type,
            created_at=context.now,
            valid_until=valid_until,
            rationale=rationale,
            context_snapshot=context.identity(),
            evidence_ids=tuple(str(item) for item in evidence_ids),
            steps=tuple(steps),
            resource_budget=resource_budget,
        )
        report = self.validate(plan, context)
        if not report.valid:
            raise PlanValidationError(report)
        self.store.create_plan(plan)
        return plan

    def edit_remove_steps(
        self,
        plan_id: str,
        *,
        remove_step_ids: Iterable[str],
        edit_id: str,
        reason: str,
        context: PlanningContext,
    ) -> StructuredPlan:
        plan = self.store.edit_remove_steps(
            plan_id,
            remove_step_ids,
            edit_id=edit_id,
            reason=reason,
        )
        report = self.validate(plan, context)
        if not report.valid:
            self.store.set_plan_status(
                plan_id, PlanStatus.INVALID, "; ".join(report.reasons)
            )
            raise PlanValidationError(report)
        return plan

    def revalidate(self, plan_id: str, context: PlanningContext) -> ValidationReport:
        plan = self.store.get_plan(plan_id)
        report = self.validate(plan, context)
        if not report.valid and plan.status is PlanStatus.ACTIVE:
            status = (
                PlanStatus.EXPIRED
                if "expired" in report.reasons
                else PlanStatus.INVALID
            )
            self.store.set_plan_status(plan_id, status, "; ".join(report.reasons))
        return report


__all__ = ["TypedPlanner"]
