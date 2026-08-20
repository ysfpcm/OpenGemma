"""Contextual grants and the Phase 6 boundary into the Guardian Kernel."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from openjarvis.cognition import ActionProposal
from openjarvis.guardian import GuardianKernel

from .models import (
    AuthorizationDecision,
    AutonomyLevel,
    ContextualGrant,
    PlanningContext,
    PlanStatus,
    StructuredPlan,
)
from .planner import TypedPlanner
from .store import PlanningStore


def _parse(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _parameter_hash(proposal: ActionProposal) -> str:
    body = json.dumps(proposal.parameters, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


class Phase6Controller:
    """Reviewable Phase 6 coordinator; it never executes a plan.

    The controller owns plan/grant semantics, then asks the existing Guardian
    Kernel to create the final authorization.  There is intentionally no
    ``execute`` method here: a plan can be inspected and authorized, but it
    cannot directly cause a side effect.
    """

    def __init__(self, guardian: GuardianKernel, store: PlanningStore) -> None:
        self.guardian = guardian
        self.store = store
        self.planner = TypedPlanner(guardian.registry, store)

    def close(self) -> None:
        self.store.close()

    def set_autonomy_level(self, action_type: str, level: AutonomyLevel) -> None:
        try:
            self.guardian.registry.get(action_type)
        except KeyError as exc:
            raise ValueError("autonomy policy must name a registered action") from exc
        self.store.set_autonomy_level(action_type, level)

    def create_plan(
        self, plan: StructuredPlan, context: PlanningContext
    ) -> StructuredPlan:
        report = self.planner.validate(plan, context)
        if not report.valid:
            raise ValueError("invalid structured plan: " + "; ".join(report.reasons))
        self.store.create_plan(plan)
        return plan

    def edit_remove_steps(
        self,
        plan_id: str,
        *,
        remove_step_ids: list[str],
        edit_id: str,
        reason: str,
        context: PlanningContext,
    ) -> StructuredPlan:
        return self.planner.edit_remove_steps(
            plan_id,
            remove_step_ids=remove_step_ids,
            edit_id=edit_id,
            reason=reason,
            context=context,
        )

    def grant(self, grant: ContextualGrant) -> ContextualGrant:
        plan = self.store.get_plan(grant.plan_id, grant.plan_version)
        step = plan.step(grant.step_id)
        target = step.proposal.parameters.get("target")
        if (
            step.proposal.action_type != grant.action_type
            or target != grant.target
            or step.capability
            != self.guardian.registry.get(grant.action_type).capability
            or step.consequence_class != grant.consequence_class
            or step.reversible != grant.reversible
            or plan.edited_by_marc != grant.edited_by_marc
        ):
            raise ValueError("grant scope does not exactly match the plan step")
        snapshot = dict(plan.context_snapshot)
        for field_name in (
            "workspace",
            "location",
            "household_mode",
            "situation_id",
            "situation_type",
            "calendar_event_id",
            "calendar_event_start",
        ):
            if getattr(grant, field_name) != snapshot.get(field_name):
                raise ValueError("grant scope does not exactly match the plan context")
        if tuple(sorted(grant.presence)) != tuple(
            sorted(str(item) for item in snapshot.get("presence", []))
        ):
            raise ValueError("grant presence does not exactly match the plan context")
        if _parse(grant.time_window_end) > _parse(plan.valid_until):
            raise ValueError("grant time window exceeds the plan expiration")
        if _parse(grant.expires_at) > _parse(plan.valid_until):
            raise ValueError("grant expiration exceeds the plan expiration")
        if not grant.required_observation_keys:
            raise ValueError("grant must declare required source observations")
        if not set(grant.required_observation_keys) <= {
            condition.observation_key
            for condition in (*step.preconditions, *step.cancellation_conditions)
        } | {item.observation_key for item in step.expected_observations}:
            raise ValueError("grant names an observation outside the plan evidence")
        self.store.put_grant(grant)
        return grant

    def revoke_grant(self, grant_id: str, reason: str = "Marc revoked grant") -> None:
        self.store.revoke_grant(grant_id, reason)

    def authorize(
        self,
        plan_id: str,
        plan_version: int,
        step_id: str,
        context: PlanningContext,
        *,
        grant_id: str | None = None,
        marc_approved: bool = False,
    ) -> AuthorizationDecision:
        existing = self.store.get_decision(plan_id, plan_version, step_id)
        try:
            plan = self.store.get_plan(plan_id, plan_version)
            step = plan.step(step_id)
        except (KeyError, ValueError) as exc:
            return self._deny(
                plan_id, plan_version, step_id, f"unknown plan or step: {exc}"
            )

        if plan.status is not PlanStatus.ACTIVE:
            return self._deny(
                plan_id, plan_version, step_id, f"plan is {plan.status.value}"
            )
        report = self.planner.validate(plan, context)
        if not report.valid:
            if plan.version == self.store.get_plan(plan_id).version:
                status = (
                    PlanStatus.EXPIRED
                    if "expired" in report.reasons
                    else PlanStatus.INVALID
                )
                self.store.set_plan_status(plan_id, status, "; ".join(report.reasons))
            return self._deny(plan_id, plan_version, step_id, "; ".join(report.reasons))

        level = self.store.autonomy_level(step.proposal.action_type)
        if level is AutonomyLevel.SHADOW:
            return self._deny(
                plan_id, plan_version, step_id, "policy is in shadow mode", level
            )
        if level is AutonomyLevel.SUGGEST:
            return self._deny(
                plan_id,
                plan_version,
                step_id,
                "policy may suggest but not authorize",
                level,
            )

        definition = self.guardian.registry.get(step.proposal.action_type)
        high_consequence = (
            step.requires_explicit_approval
            or definition.consequence_class.lower() in {"high", "critical", "security"}
            or not step.reversible
        )
        if high_consequence and not marc_approved:
            return self._deny(
                plan_id,
                plan_version,
                step_id,
                "high-consequence action requires separate Marc approval",
                level,
            )

        grant: ContextualGrant | None = None
        if level in {AutonomyLevel.DELEGATED, AutonomyLevel.MATURE_ROUTINE}:
            if not grant_id:
                return self._deny(
                    plan_id,
                    plan_version,
                    step_id,
                    "no contextual grant supplied",
                    level,
                )
            try:
                grant = self.store.get_grant(grant_id)
            except KeyError:
                return self._deny(
                    plan_id,
                    plan_version,
                    step_id,
                    "contextual grant not found",
                    level,
                )
            reason = self._grant_failure(grant, plan, step, context)
            if reason:
                return self._deny(
                    plan_id, plan_version, step_id, reason, level, grant_id
                )
            if existing is not None and existing.allowed:
                return existing
            reserved, reservation_reason = self.store.reserve_grant_usage(
                grant, step.proposal.id, context.now_datetime
            )
            if not reserved:
                return self._deny(
                    plan_id, plan_version, step_id, reservation_reason, level, grant_id
                )
        elif not marc_approved:
            return self._deny(
                plan_id,
                plan_version,
                step_id,
                "explicit Marc approval is required",
                level,
            )
        elif existing is not None and existing.allowed:
            return existing

        guardian_grant_id = (
            f"phase6:{plan_id}:{plan_version}:{step_id}:{step.proposal.id}"
        )
        try:
            try:
                expires = grant.expires_at if grant else step.expires_at
                seconds = max(
                    1,
                    int((_parse(expires) - datetime.now(timezone.utc)).total_seconds()),
                )
                self.guardian.grant(
                    grant_id=guardian_grant_id,
                    session_id=f"phase6:{plan_id}:{plan_version}",
                    capability=definition.capability,
                    scope={
                        "action_type": step.proposal.action_type,
                        "target": step.proposal.parameters.get("target"),
                        "parameters_hash": _parameter_hash(step.proposal),
                    },
                    # The Guardian grant must live until the contextual scope's
                    # exact expiry even when a deterministic fixture uses a clock
                    # ahead of the process clock.  Its scope is still exact and
                    # cannot be reused for another plan step.
                    expires_in_seconds=seconds,
                )
            except sqlite3.IntegrityError as exc:
                if "guardian_grants.grant_id" not in str(exc):
                    raise
                # A restart/replay may have already installed the same exact,
                # short-lived Guardian grant.  Reusing that exact scope is safe.

            authority = "Marc" if marc_approved else "Phase6.ContextualGrant"
            decision = self.guardian.authorize(
                step.proposal,
                session_id=f"phase6:{plan_id}:{plan_version}",
                authority=authority,
            )
        except Exception:
            if grant is not None:
                self.store.release_grant_usage(grant, step.proposal.id)
            raise
        if not decision.allowed:
            if grant is not None:
                self.store.release_grant_usage(grant, step.proposal.id)
            return self._deny(
                plan_id,
                plan_version,
                step_id,
                decision.reason or "Guardian denied the action",
                level,
                grant_id,
            )
        if (
            decision.authorization.scope.get("session_id")
            != (f"phase6:{plan_id}:{plan_version}")
            or decision.authorization.scope.get("grant_id") != guardian_grant_id
        ):
            if grant is not None:
                self.store.release_grant_usage(grant, step.proposal.id)
            return self._deny(
                plan_id,
                plan_version,
                step_id,
                "Guardian returned an authorization for a different exact scope",
                level,
                grant_id,
            )
        result = AuthorizationDecision(
            allowed=True,
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id,
            reason="authorized by exact Phase 6 scope",
            authorization=decision.authorization,
            grant_id=grant_id,
            autonomy_level=level,
            evidence=tuple(
                (
                    *plan.evidence_ids,
                    *[item.source_id for item in context.observations.values()],
                )
            ),
        )
        self.store.save_decision(result)
        return result

    def _grant_failure(
        self,
        grant: ContextualGrant,
        plan: StructuredPlan,
        step: Any,
        context: PlanningContext,
    ) -> str:
        now = context.now_datetime
        if grant.revoked_at:
            return "contextual grant was revoked"
        if _parse(grant.expires_at) <= now:
            return "contextual grant expired"
        if not (
            _parse(grant.time_window_start) <= now <= _parse(grant.time_window_end)
        ):
            return "outside grant time window"
        if grant.action_type != step.proposal.action_type:
            return "grant action is out of scope"
        if grant.target != step.proposal.parameters.get("target"):
            return "grant target is out of scope"
        if grant.workspace != context.workspace:
            return "grant workspace is out of scope"
        if grant.location != context.location:
            return "grant location is out of scope"
        if grant.household_mode != context.household_mode:
            return "grant household mode is out of scope"
        if (
            grant.situation_id != context.situation_id
            or grant.situation_type != context.situation_type
        ):
            return "grant situation is out of scope"
        if grant.calendar_event_id != (
            context.calendar_event_id or ""
        ) or grant.calendar_event_start != (context.calendar_event_start or ""):
            return "grant calendar context is out of scope"
        if (
            grant.plan_id != plan.plan_id
            or grant.plan_version != plan.version
            or grant.step_id != step.step_id
        ):
            return "grant plan version or step is out of scope"
        if (
            grant.reversible != step.reversible
            or grant.consequence_class != step.consequence_class
        ):
            return "grant consequence class is out of scope"
        if grant.edited_by_marc != plan.edited_by_marc:
            return "grant edit authority is out of scope"
        if tuple(sorted(grant.presence)) != tuple(sorted(context.presence)):
            return "grant presence condition is out of scope"
        if context.confidence < grant.min_confidence:
            return "context confidence is below grant requirement"
        for key in grant.required_observation_keys:
            observation = context.observations.get(key)
            if observation is None:
                return f"required source observation missing: {key}"
            if observation.age_seconds(now) > grant.max_source_age_seconds:
                return f"required source observation is stale: {key}"
            if observation.confidence < grant.min_confidence:
                return f"required source confidence is below grant requirement: {key}"
        return ""

    def _deny(
        self,
        plan_id: str,
        plan_version: int,
        step_id: str,
        reason: str,
        level: AutonomyLevel = AutonomyLevel.SHADOW,
        grant_id: str | None = None,
    ) -> AuthorizationDecision:
        decision = AuthorizationDecision(
            allowed=False,
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id,
            reason=reason,
            grant_id=grant_id,
            autonomy_level=level,
        )
        self.store.save_decision(decision)
        return decision

    def inspect_plan(
        self, plan_id: str, context: PlanningContext | None = None
    ) -> dict[str, Any]:
        plan = self.store.get_plan(plan_id)
        report = self.planner.validate(plan, context) if context else None
        return {
            "plan": plan.to_dict(),
            "versions": [item.to_dict() for item in self.store.list_versions(plan_id)],
            "edits": self.store.edit_history(plan_id),
            "grants": [
                item.to_dict()
                for item in self.store.list_grants()
                if item.plan_id == plan_id
            ],
            "decisions": [
                item.to_dict() for item in self.store.list_decisions(plan_id)
            ],
            "scheduled_effects": self.store.scheduled_effects(plan_id),
            "validation": report.to_dict() if report else None,
            "audit": self.store.audit(plan_id),
            "side_effects": False,
        }

    def cancel_plan(self, plan_id: str, reason: str = "Marc canceled plan") -> None:
        self.store.set_plan_status(plan_id, PlanStatus.CANCELED, reason)


__all__ = ["Phase6Controller"]
