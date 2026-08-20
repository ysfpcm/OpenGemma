"""Phase 7 orchestration: calculate, review, authorize, delay, execute, verify."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping

from openjarvis.cognition import ActionProposal
from openjarvis.guardian import GuardianKernel
from openjarvis.planning import (
    AutonomyLevel,
    Condition,
    ContextualGrant,
    ExpectedObservation,
    Phase6Controller,
    PlanningContext,
    PlanStatus,
    PlanStep,
    StructuredPlan,
)

from .models import (
    DepartureAction,
    DepartureObservations,
    DepartureSetup,
    RecalculationResult,
    WhyNowBrief,
    _parse,
    _timestamp,
)
from .store import DepartureStore


@dataclass(frozen=True, slots=True)
class Phase7Flags:
    enabled: bool = False
    delayed_execution_enabled: bool = False

    @classmethod
    def from_environment(cls) -> "Phase7Flags":
        return cls(
            enabled=os.environ.get("OPHANIM_PHASE_7_ENABLED", "0") == "1",
            delayed_execution_enabled=os.environ.get(
                "OPHANIM_PHASE_7_DELAYED_EXECUTION", "0"
            )
            == "1",
        )


class DepartureGuardian:
    """Review-first Departure coordinator with a hard Guardian boundary."""

    def __init__(
        self,
        guardian: GuardianKernel,
        planning: Phase6Controller,
        store: DepartureStore,
        *,
        flags: Phase7Flags | None = None,
    ) -> None:
        self.guardian = guardian
        self.planning = planning
        self.store = store
        self.flags = flags or Phase7Flags()

    def close(self) -> None:
        self.store.close()

    def save_setup(self, setup: DepartureSetup) -> DepartureSetup:
        # Phase 7 policies always begin in shadow.  A later explicit approval
        # or contextual grant may raise one action at a time.
        for action in setup.supported_actions:
            if action.enabled:
                self.planning.set_autonomy_level(
                    action.action_type, AutonomyLevel.SHADOW
                )
        prior_states = self.store.list_departure_states(setup_id=setup.setup_id)
        saved = self.store.save_setup(setup)
        if saved:
            for state in prior_states:
                if int(state.get("setup_version") or 0) >= setup.version:
                    continue
                plan_id = state.get("plan_id")
                if plan_id:
                    try:
                        plan = self.planning.store.get_plan(plan_id)
                    except KeyError:
                        plan = None
                    if plan is not None and plan.status is PlanStatus.ACTIVE:
                        self.planning.store.set_plan_status(
                            plan_id,
                            PlanStatus.SUPERSEDED,
                            f"setup version {setup.version} superseded the prior setup",
                        )
                self.store.cancel_schedules(
                    departure_id=state["departure_id"],
                    reason=f"setup version {setup.version} requires recalculation",
                )
                self.store.save_departure_state(
                    state["departure_id"],
                    status="blocked",
                    reason=f"setup version {setup.version} requires recalculation",
                )
                self.store.record_event(
                    "setup-invalidated-departure",
                    departure_id=state["departure_id"],
                    plan_id=plan_id,
                    details={"setup_version": setup.version},
                )
        return self.store.get_setup(setup.setup_id, setup.version)

    def setup(self, setup_id: str, version: int | None = None) -> DepartureSetup:
        return self.store.get_setup(setup_id, version)

    def recalculate(
        self,
        setup_id: str,
        observations: DepartureObservations,
        *,
        reason: str = "observation update",
    ) -> RecalculationResult:
        setup = self.store.get_setup(setup_id)
        self._ensure_setup_actions_registered(setup)
        brief = self._brief(setup, observations)
        state = self.store.get_departure_state(observations.departure_id)
        prior_plan_id = state.get("plan_id")
        current_fingerprint = self._fingerprint(setup, observations)
        if (
            state
            and state.get("context_fingerprint") == current_fingerprint
            and prior_plan_id
        ):
            try:
                plan = self.planning.store.get_plan(prior_plan_id)
            except KeyError:
                plan = None
            if (
                state.get("status") == "active"
                and plan is not None
                and plan.status is PlanStatus.ACTIVE
            ):
                self.store.record_event(
                    "recalculation-unchanged",
                    departure_id=observations.departure_id,
                    plan_id=prior_plan_id,
                    details={"reason": reason, "plan_version": plan.version},
                )
                return RecalculationResult(
                    observations.departure_id,
                    "unchanged",
                    plan.plan_id,
                    plan.version,
                    (),
                    brief,
                    self.planning.planner.validate(
                        plan, self._planning_context(setup, observations)
                    ).to_dict(),
                )

        warnings = list(observations.source_warnings(setup))
        if observations.calendar_status.lower() in {"canceled", "cancelled"}:
            warnings.append("calendar event canceled")
        if observations.marc_present is False:
            warnings.append("Marc is not present at the configured location")
        if observations.conflicting_plan_id:
            warnings.append("conflicting departure plan is active")

        if prior_plan_id:
            self._supersede_prior(
                prior_plan_id, observations.departure_id, "; ".join(warnings) or reason
            )

        if warnings:
            self.store.save_departure_state(
                observations.departure_id,
                setup_id=setup_id,
                setup_version=setup.version,
                plan_id=prior_plan_id,
                plan_version=state.get("plan_version"),
                context_fingerprint=current_fingerprint,
                status="blocked",
                reason="; ".join(dict.fromkeys(warnings)),
            )
            self.store.record_event(
                "recalculation-blocked",
                departure_id=observations.departure_id,
                plan_id=prior_plan_id,
                details={"warnings": list(dict.fromkeys(warnings)), "reason": reason},
            )
            return RecalculationResult(
                observations.departure_id,
                "blocked",
                None,
                None,
                tuple(dict.fromkeys(warnings)),
                brief,
            )

        context = self._planning_context(setup, observations)
        expected_departure = self._expected_departure(setup, observations)
        if _parse(observations.calendar_event_start) <= observations.now_datetime:
            reasons = ("calendar event has already started",)
            self.store.save_departure_state(
                observations.departure_id,
                setup_id=setup_id,
                setup_version=setup.version,
                context_fingerprint=current_fingerprint,
                status="blocked",
                reason=reasons[0],
            )
            return RecalculationResult(
                observations.departure_id, "blocked", None, None, reasons, brief
            )

        plan_id = str(prior_plan_id or f"departure-plan-{observations.departure_id}")
        prior = self.planning.store.get_plan(plan_id) if prior_plan_id else None
        version = (prior.version + 1) if prior else 1
        removed = prior.removed_step_ids if prior else ()
        steps = self._steps(setup, observations, version, removed)
        plan = StructuredPlan(
            plan_id=plan_id,
            version=version,
            goal_id=f"departure-goal-{observations.departure_id}",
            situation_id=f"departure-situation-{observations.departure_id}",
            situation_type="Departure",
            created_at=observations.now,
            valid_until=observations.calendar_event_start,
            rationale="Departure Guardian prepares the configured routine from typed observations; imported text is informational only.",
            context_snapshot={
                **context.identity(),
                "expected_departure_at": expected_departure,
            },
            evidence_ids=tuple(observations.evidence_ids),
            steps=tuple(steps),
            resource_budget=float(max(1, len(steps))),
            removed_step_ids=removed,
            edited_by_marc=bool(prior.edited_by_marc) if prior else False,
        )
        self.planning.create_plan(plan, context)
        if prior_plan_id:
            self.store.cancel_schedules(
                plan_id=prior_plan_id, reason="superseded by recalculation"
            )
        self.store.save_departure_state(
            observations.departure_id,
            setup_id=setup_id,
            setup_version=setup.version,
            plan_id=plan_id,
            plan_version=version,
            context_fingerprint=current_fingerprint,
            status="active",
            reason="",
        )
        self.store.record_event(
            "recalculation",
            departure_id=observations.departure_id,
            plan_id=plan_id,
            details={
                "version": version,
                "expected_departure_at": expected_departure,
                "reason": reason,
            },
        )
        self.store.record_event(
            "brief-generated",
            departure_id=observations.departure_id,
            plan_id=plan_id,
            details=brief.to_dict(),
        )
        report = self.planning.planner.validate(plan, context)
        return RecalculationResult(
            observations.departure_id,
            "created" if version == 1 else "superseded-and-created",
            plan_id,
            version,
            (),
            brief,
            report.to_dict(),
        )

    def inspect(self, departure_id: str) -> dict[str, Any]:
        state = self.store.get_departure_state(departure_id)
        result: dict[str, Any] = {
            "departure": state,
            "schedules": self.store.list_schedules(departure_id=departure_id),
            "events": self.store.events(departure_id=departure_id),
            "side_effects": False,
        }
        if state.get("plan_id"):
            result["plan"] = self.planning.inspect_plan(state["plan_id"])
        return result

    def edit_plan(
        self,
        departure_id: str,
        *,
        remove_step_ids: list[str],
        edit_id: str,
        reason: str,
        observations: DepartureObservations,
    ) -> StructuredPlan:
        state = self.store.get_departure_state(departure_id)
        plan_id = state.get("plan_id")
        if not plan_id:
            raise KeyError(departure_id)
        setup = self.store.get_setup(state["setup_id"])
        plan = self.planning.edit_remove_steps(
            plan_id,
            remove_step_ids=remove_step_ids,
            edit_id=edit_id,
            reason=reason,
            context=self._planning_context(setup, observations),
        )
        self.store.cancel_schedules(
            plan_id=plan_id, reason="scheduled effects invalidated by Marc edit"
        )
        self.store.save_departure_state(
            departure_id,
            plan_version=plan.version,
            status="active",
            reason="edited by Marc",
        )
        self.store.record_event(
            "plan-edited",
            departure_id=departure_id,
            plan_id=plan_id,
            details={
                "version": plan.version,
                "removed": remove_step_ids,
                "edit_id": edit_id,
            },
        )
        return plan

    def approve_step(
        self,
        departure_id: str,
        step_id: str,
        observations: DepartureObservations,
        *,
        approval_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        state = self.store.get_departure_state(departure_id)
        if not state.get("plan_id"):
            raise KeyError(departure_id)
        setup = self.store.get_setup(state["setup_id"])
        plan = self.planning.store.get_plan(state["plan_id"])
        step = plan.step(step_id)
        approval_id = approval_id or f"approval:{plan.plan_id}:{plan.version}:{step_id}"
        expires_at = expires_at or step.expires_at
        _parse(expires_at)
        try:
            existing_approval = self.store.get_approval(approval_id)
        except KeyError:
            existing_approval = None
        if existing_approval is not None and (
            existing_approval["plan_id"],
            int(existing_approval["plan_version"]),
            existing_approval["step_id"],
        ) != (plan.plan_id, plan.version, step_id):
            raise ValueError("approval ID is already bound to a different plan step")
        self.store.save_approval(
            approval_id, plan.plan_id, plan.version, step_id, expires_at
        )
        # This is an explicit Marc action.  It does not make the routine
        # autonomous; it only permits this exact step to reach Phase 6's
        # APPROVE branch.
        self.planning.set_autonomy_level(
            step.proposal.action_type, AutonomyLevel.APPROVE
        )
        self.store.record_event(
            "approval-created",
            departure_id=departure_id,
            plan_id=plan.plan_id,
            details={
                "approval_id": approval_id,
                "step_id": step_id,
                "setup_version": setup.version,
            },
        )
        return self.store.get_approval(approval_id)

    def grant(self, grant: ContextualGrant) -> ContextualGrant:
        stored = self.planning.grant(grant)
        self.planning.set_autonomy_level(grant.action_type, AutonomyLevel.DELEGATED)
        self.store.record_event(
            "grant-created",
            plan_id=grant.plan_id,
            details={"grant_id": grant.grant_id, "step_id": grant.step_id},
        )
        return stored

    def revoke_grant(self, grant_id: str) -> None:
        self.planning.revoke_grant(grant_id)
        for item in self.store.list_schedules():
            if item.get("grant_id") == grant_id:
                self.store.mark_schedule(
                    item["effect_id"], "canceled", reason="contextual grant revoked"
                )
        self.store.record_event("grant-revoked", details={"grant_id": grant_id})

    def revoke_approval(
        self, approval_id: str, reason: str = "Marc revoked Departure approval"
    ) -> None:
        self.store.revoke_approval(approval_id, reason)
        for item in self.store.list_schedules():
            if item.get("approval_id") == approval_id:
                self.store.mark_schedule(item["effect_id"], "canceled", reason=reason)
        self.store.record_event(
            "approval-revoked", details={"approval_id": approval_id, "reason": reason}
        )

    def schedule_plan(
        self,
        departure_id: str,
        *,
        grant_ids: Mapping[str, str] | None = None,
        approval_ids: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        state = self.store.get_departure_state(departure_id)
        if not state.get("plan_id"):
            raise KeyError(departure_id)
        plan = self.planning.store.get_plan(state["plan_id"], state["plan_version"])
        if plan.status is not PlanStatus.ACTIVE:
            raise ValueError(f"cannot schedule a {plan.status.value} plan")
        step_ids = {step.step_id for step in plan.steps}
        for name, values in (("grant", grant_ids), ("approval", approval_ids)):
            unknown = set(values or {}) - step_ids
            if unknown:
                raise ValueError(
                    f"{name} IDs name unknown plan steps: {sorted(unknown)}"
                )
        setup = self.store.get_setup(state["setup_id"])
        due_at = plan.context_snapshot.get("expected_departure_at")
        if not due_at:
            due_at = _timestamp(
                _parse(
                    plan.context_snapshot.get("calendar_event_start", plan.valid_until)
                )
                - timedelta(minutes=setup.preparation_buffer.minutes)
            )
        for step in plan.steps:
            effect_id = (
                f"departure-effect:{plan.plan_id}:v{plan.version}:{step.step_id}"
            )
            self.store.schedule_effect(
                effect_id=effect_id,
                departure_id=departure_id,
                plan_id=plan.plan_id,
                plan_version=plan.version,
                step_id=step.step_id,
                due_at=due_at,
                grant_id=(grant_ids or {}).get(step.step_id),
                approval_id=(approval_ids or {}).get(step.step_id),
            )
        self.store.record_event(
            "plan-scheduled",
            departure_id=departure_id,
            plan_id=plan.plan_id,
            details={"version": plan.version, "due_at": due_at},
        )
        return self.store.list_schedules(departure_id=departure_id)

    def run_due(
        self, departure_id: str, observations: DepartureObservations
    ) -> list[dict[str, Any]]:
        schedules = self.store.list_schedules(departure_id=departure_id)
        if not self.flags.enabled or not self.flags.delayed_execution_enabled:
            self.store.record_event(
                "execution-disabled",
                departure_id=departure_id,
                details={
                    "enabled": self.flags.enabled,
                    "delayed_execution_enabled": self.flags.delayed_execution_enabled,
                },
            )
            return schedules
        if not observations.actual_departure_detected:
            return schedules
        state = self.store.get_departure_state(departure_id)
        if not state.get("plan_id"):
            return schedules
        setup = self.store.get_setup(state["setup_id"])
        if not setup.policy.execution_enabled:
            self.store.record_event(
                "execution-disabled",
                departure_id=departure_id,
                details={"reason": "setup policy keeps delayed execution disabled"},
            )
            return schedules
        warnings = observations.source_warnings(setup)
        if warnings:
            reason = "required source is stale or unavailable: " + "; ".join(warnings)
            self.store.cancel_schedules(departure_id=departure_id, reason=reason)
            self.store.save_departure_state(
                departure_id, status="blocked", reason=reason
            )
            self.store.record_event(
                "recalculation-blocked",
                departure_id=departure_id,
                plan_id=state.get("plan_id"),
                details={"warnings": list(warnings), "at_execution": True},
            )
            return self.store.list_schedules(departure_id=departure_id)
        if getattr(self.guardian, "_stopped", lambda: False)():
            self.store.cancel_schedules(
                departure_id=departure_id, reason="emergency stop"
            )
            self.store.record_event(
                "emergency-stop",
                departure_id=departure_id,
                details={"at_execution": True},
            )
            return self.store.list_schedules(departure_id=departure_id)
        context = self._planning_context(setup, observations)
        for item in schedules:
            if item["status"] not in {"scheduled", "pending"}:
                if item["status"] in {"completed", "failed", "ambiguous"}:
                    self.store.record_event(
                        "duplicate-prevented",
                        departure_id=departure_id,
                        plan_id=item["plan_id"],
                        details={
                            "effect_id": item["effect_id"],
                            "status": item["status"],
                        },
                    )
                continue
            reason = self._cancellation_reason(observations)
            if reason:
                self.store.mark_schedule(item["effect_id"], "canceled", reason=reason)
                continue
            try:
                current = self.planning.store.get_plan(item["plan_id"])
            except KeyError:
                self.store.mark_schedule(
                    item["effect_id"], "canceled", reason="plan no longer exists"
                )
                continue
            if (
                current.version != item["plan_version"]
                or current.status is not PlanStatus.ACTIVE
            ):
                self.store.mark_schedule(
                    item["effect_id"],
                    "canceled",
                    reason="stale or superseded plan version",
                )
                continue
            self.store.mark_schedule(item["effect_id"], "executing")
            approval = None
            if item.get("approval_id"):
                try:
                    approval = self.store.get_approval(item["approval_id"])
                except KeyError:
                    self.store.mark_schedule(
                        item["effect_id"], "skipped", reason="approval record missing"
                    )
                    continue
                if (
                    approval["plan_id"],
                    int(approval["plan_version"]),
                    approval["step_id"],
                ) != (item["plan_id"], item["plan_version"], item["step_id"]):
                    self.store.mark_schedule(
                        item["effect_id"],
                        "skipped",
                        reason="approval scope does not match scheduled effect",
                    )
                    self.store.record_event(
                        "approval-scope-mismatch",
                        departure_id=departure_id,
                        plan_id=item["plan_id"],
                        details={"effect_id": item["effect_id"]},
                    )
                    continue
                if approval["revoked_at"] or _parse(approval["expires_at"]) <= _parse(
                    observations.now
                ):
                    self.store.mark_schedule(
                        item["effect_id"],
                        "skipped",
                        reason="approval expired or revoked",
                    )
                    continue
            decision = self.planning.authorize(
                item["plan_id"],
                item["plan_version"],
                item["step_id"],
                context,
                grant_id=item.get("grant_id"),
                marc_approved=approval is not None,
            )
            if not decision.allowed or decision.authorization is None:
                event = "authorization-denied"
                self.store.mark_schedule(
                    item["effect_id"],
                    "skipped",
                    reason=decision.reason,
                    result=decision.to_dict(),
                )
                self.store.record_event(
                    event,
                    departure_id=departure_id,
                    plan_id=item["plan_id"],
                    details={"step_id": item["step_id"], "reason": decision.reason},
                )
                continue
            step = current.step(item["step_id"])
            result = self.guardian.execute(step.proposal.id, decision.authorization)
            result_body = {
                "state": result.state.value,
                "verification": result.verification.to_dict()
                if result.verification
                else None,
                "exception_summary": result.exception_summary,
                "outcome": {
                    "success": result.outcome.success,
                    "response": result.outcome.response,
                    "error": result.outcome.error.value
                    if result.outcome.error
                    else None,
                },
            }
            if result.state.value == "verified":
                self.store.mark_schedule(
                    item["effect_id"],
                    "completed",
                    action_id=step.proposal.id,
                    result=result_body,
                )
            elif result.state.value == "needs_attention":
                self.store.mark_schedule(
                    item["effect_id"],
                    "ambiguous",
                    action_id=step.proposal.id,
                    reason=result.exception_summary or "ambiguous effect",
                    result=result_body,
                )
            else:
                self.store.mark_schedule(
                    item["effect_id"],
                    "failed",
                    action_id=step.proposal.id,
                    reason=result.exception_summary or "action failed",
                    result=result_body,
                )
        return self.store.list_schedules(departure_id=departure_id)

    def cancel(
        self, departure_id: str, reason: str = "Marc canceled Departure"
    ) -> None:
        state = self.store.get_departure_state(departure_id)
        if state.get("plan_id"):
            self.planning.cancel_plan(state["plan_id"], reason)
        self.store.cancel_schedules(departure_id=departure_id, reason=reason)
        self.store.save_departure_state(departure_id, status="canceled", reason=reason)
        self.store.record_event(
            "plan-canceled",
            departure_id=departure_id,
            plan_id=state.get("plan_id"),
            details={"reason": reason},
        )

    def emergency_stop(self, reason: str = "Marc requested emergency stop") -> None:
        self.guardian.emergency_stop(reason=reason)
        self.store.cancel_schedules(reason="emergency stop")
        self.store.record_event("emergency-stop", details={"reason": reason})

    def completion_report(self, departure_id: str) -> dict[str, Any] | None:
        schedules = self.store.list_schedules(departure_id=departure_id)
        exceptions = [
            item
            for item in schedules
            if item["status"] in {"failed", "ambiguous", "skipped", "canceled"}
        ]
        if not exceptions:
            return None
        report = {
            "departure_id": departure_id,
            "exception_only": True,
            "summary": f"Departure routine had {len(exceptions)} exception(s).",
            "failed_or_ambiguous": [
                item for item in exceptions if item["status"] in {"failed", "ambiguous"}
            ],
            "skipped": [
                {"step_id": item["step_id"], "reason": item["reason"]}
                for item in exceptions
                if item["status"] == "skipped"
            ],
            "canceled": [
                {"step_id": item["step_id"], "reason": item["reason"]}
                for item in exceptions
                if item["status"] == "canceled"
            ],
            "verification_results": [
                item.get("result", {}).get("verification")
                for item in exceptions
                if item.get("result")
            ],
            "stale_or_unavailable_evidence": [
                event
                for event in self.store.events(departure_id=departure_id)
                if event["event"] == "recalculation-blocked"
            ],
            "timeline": self.store.events(departure_id=departure_id),
        }
        self.store.save_report(f"completion:{departure_id}", "exception-only", report)
        return report

    def weekly_trust_report(self, week_start: str) -> dict[str, Any]:
        report = self.store.weekly_trust_report(week_start)
        self.store.save_report(f"trust:{week_start}", "weekly-trust", report)
        return report

    def _ensure_setup_actions_registered(self, setup: DepartureSetup) -> None:
        for action in setup.supported_actions:
            if action.enabled:
                self.guardian.registry.get(action.action_type)

    def _planning_context(
        self, setup: DepartureSetup, observations: DepartureObservations
    ) -> PlanningContext:
        return PlanningContext(
            now=observations.now,
            situation_id=f"departure-situation-{observations.departure_id}",
            situation_type="Departure",
            workspace=observations.workspace,
            location=observations.location,
            household_mode=observations.household_mode,
            observations=observations.to_planning_observations(setup),
            calendar_event_id=observations.calendar_event_id,
            calendar_event_start=observations.calendar_event_start,
            presence=tuple(observations.present_people),
            confidence=min(observations.source_confidence.values(), default=1.0),
        )

    def _steps(
        self,
        setup: DepartureSetup,
        observations: DepartureObservations,
        version: int,
        removed: tuple[str, ...],
    ) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for index, action in enumerate(setup.supported_actions):
            if not action.enabled or f"action-{index}" in removed:
                continue
            try:
                definition = self.guardian.registry.get(action.action_type)
            except KeyError as exc:
                raise ValueError(
                    f"Departure action is not registered: {action.action_type}"
                ) from exc
            step_id = f"action-{index}"
            proposal = ActionProposal(
                id=f"departure-action:{observations.departure_id}:v{version}:{step_id}",
                action_type=action.action_type,
                description=f"Departure Guardian action for {action.target}",
                parameters=action.proposal_parameters(),
                idempotency_key=f"departure:{observations.departure_id}:v{version}:{step_id}",
                expected_effect=definition.expected_effect,
                provenance={
                    "component": "phase7-departure",
                    "untrusted_text_is_informational": True,
                },
                causal_parents=list(observations.evidence_ids),
            )
            preconditions = tuple(
                Condition(
                    name=name,
                    observation_key=name,
                    expected=True,
                    max_age_seconds=self._freshness(setup, name),
                )
                for name in (
                    "calendar_active",
                    "traffic_fresh",
                    "weather_fresh",
                    "presence_fresh",
                    "household_stable",
                )
            )
            cancellations = tuple(
                Condition(
                    name=name,
                    observation_key=name,
                    expected=True,
                    max_age_seconds=self._freshness(setup, name),
                )
                for name in ("calendar_canceled", "return_home", "manual_reversal")
            )
            expected = (
                ExpectedObservation(
                    f"verify-{step_id}",
                    f"verified:{action.target}",
                    f"Independent verification for {action.target}",
                ),
            )
            steps.append(
                PlanStep(
                    step_id=step_id,
                    proposal=proposal,
                    executor=action.action_type,
                    capability=definition.capability,
                    consequence_class=definition.consequence_class,
                    reversible=definition.compensation is not None,
                    dependencies=(),
                    preconditions=preconditions,
                    expected_observations=expected,
                    expires_at=observations.calendar_event_start,
                    rationale=f"Configured Departure action for {action.target}; exact approval remains required.",
                    cancellation_conditions=cancellations,
                    resource_cost=1.0,
                    requires_explicit_approval=(
                        action.requires_explicit_approval
                        or definition.consequence_class.lower()
                        in {"high", "critical", "security"}
                        or definition.compensation is None
                    ),
                )
            )
        return steps

    @staticmethod
    def _freshness(setup: DepartureSetup, key: str) -> int:
        return {
            "calendar_active": setup.calendar.freshness_seconds,
            "traffic_fresh": setup.traffic.freshness_seconds,
            "weather_fresh": setup.weather.freshness_seconds,
            "presence_fresh": setup.presence.freshness_seconds,
            "household_stable": setup.presence.freshness_seconds,
            "calendar_canceled": setup.calendar.freshness_seconds,
            "return_home": setup.presence.freshness_seconds,
            "manual_reversal": 86_400,
        }[key]

    @staticmethod
    def _expected_departure(
        setup: DepartureSetup, observations: DepartureObservations
    ) -> str:
        minutes = setup.preparation_buffer.minutes + (observations.traffic_minutes or 0)
        return _timestamp(
            _parse(observations.calendar_event_start) - timedelta(minutes=minutes)
        )

    def _brief(
        self, setup: DepartureSetup, observations: DepartureObservations
    ) -> WhyNowBrief:
        expected = self._expected_departure(setup, observations)
        active = not observations.source_warnings(setup) and _parse(
            observations.now
        ) >= _parse(expected)
        return WhyNowBrief(
            departure_id=observations.departure_id,
            active=active,
            why_active=(
                f"{observations.calendar_title or 'Calendar event'} is scheduled for {observations.calendar_event_start}"
                if active
                else "The configured Departure situation is not yet ready for action"
            ),
            expected_departure_at=expected,
            calendar_evidence=(
                f"{observations.calendar_event_id}: {observations.calendar_status}",
                observations.destination,
            ),
            traffic_weather_evidence=(
                f"traffic: {observations.traffic_minutes} minutes"
                if observations.traffic_minutes is not None
                else "traffic unavailable",
                f"weather: {observations.weather_summary}"
                if observations.weather_summary
                else "weather unavailable",
            ),
            presence_evidence=(
                f"{setup.presence.person} present: {observations.marc_present}",
                f"people present: {', '.join(observations.present_people) or 'none'}",
            ),
            preparation_assumptions=(
                f"{setup.preparation_buffer.minutes} minute preparation buffer",
                f"travel estimate uses route {setup.destination.route_id}",
            ),
            proposed_actions=tuple(
                action.to_dict() for action in setup.supported_actions if action.enabled
            ),
            expected_effects=tuple(
                f"verified effect for {action.target}"
                for action in setup.supported_actions
                if action.enabled
            ),
            exact_authority_required=tuple(
                f"{action.action_type} on {action.target}, exact plan version, fresh sources, and {self._authority_phrase(action)}"
                for action in setup.supported_actions
                if action.enabled
            ),
            expiration_conditions=(
                "calendar event starts",
                "required source becomes stale",
                "plan is superseded or canceled",
            ),
            cancellation_conditions=(
                "Marc cancels or revokes",
                "early departure or return home",
                "calendar cancellation",
                "emergency stop",
                "household mode conflict",
            ),
            uncertainty_warnings=tuple(observations.source_warnings(setup)),
        )

    def _authority_phrase(self, action: DepartureAction) -> str:
        definition = self.guardian.registry.get(action.action_type)
        separate = (
            action.requires_explicit_approval
            or definition.consequence_class.lower() in {"high", "critical", "security"}
            or definition.compensation is None
        )
        return (
            "separate Marc approval"
            if separate
            else "configured contextual grant or explicit approval"
        )

    def _fingerprint(
        self, setup: DepartureSetup, observations: DepartureObservations
    ) -> str:
        body = {"setup_version": setup.version, "observations": observations.to_dict()}
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, default=str).encode()
        ).hexdigest()

    def _supersede_prior(self, plan_id: str, departure_id: str, reason: str) -> None:
        try:
            plan = self.planning.store.get_plan(plan_id)
        except KeyError:
            return
        if plan.status is PlanStatus.ACTIVE:
            self.planning.store.set_plan_status(plan_id, PlanStatus.SUPERSEDED, reason)
            self.store.record_event(
                "plan-superseded",
                departure_id=departure_id,
                plan_id=plan_id,
                details={"reason": reason, "version": plan.version},
            )
        self.store.cancel_schedules(plan_id=plan_id, reason="stale plan superseded")

    @staticmethod
    def _cancellation_reason(observations: DepartureObservations) -> str:
        if observations.return_home:
            return "return home detected"
        if observations.manual_reversal:
            return "Marc manually reversed the routine"
        if observations.calendar_status.lower() in {"canceled", "cancelled"}:
            return "calendar event canceled"
        if observations.conflicting_plan_id:
            return "conflicting plan is active"
        return ""


__all__ = ["DepartureGuardian", "Phase7Flags"]
