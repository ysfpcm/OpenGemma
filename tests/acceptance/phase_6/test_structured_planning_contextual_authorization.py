"""Deterministic Phase 6 Departure plan and fail-closed authorization tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.cognition import (
    ActionLedger,
    ActionProposal,
    ExecutionOutcome,
)
from openjarvis.guardian import (
    ActionDefinition,
    ActionRegistry,
    GuardianKernel,
    PreconditionResult,
)
from openjarvis.planning import (
    AutonomyLevel,
    Condition,
    ContextualGrant,
    ExpectedObservation,
    ObservationSnapshot,
    Phase6Controller,
    PlanningContext,
    PlanningStore,
    PlanStep,
    StructuredPlan,
)

BASE = datetime(2026, 8, 17, 7, 45, tzinfo=timezone.utc)


def _definition(action_type: str, consequence: str = "low", reversible: bool = True):
    world: dict[str, int] = {"effects": 0}

    def execute(_: ActionProposal) -> ExecutionOutcome:
        world["effects"] += 1
        return ExecutionOutcome(True, {"effects": world["effects"]})

    def verify(_: ActionProposal):
        return True, {"effects": world["effects"]}

    def compensate(_: ActionProposal) -> ExecutionOutcome:
        world["effects"] -= 1
        return ExecutionOutcome(True, {"effects": world["effects"]})

    return ActionDefinition(
        action_type=action_type,
        input_schema={
            "type": "object",
            "required": ["target"],
            "properties": {"target": {"type": "string"}},
            "additionalProperties": False,
        },
        capability="fixture.home.write",
        risk_class="reversible" if reversible else "high",
        consequence_class=consequence,
        preconditions=lambda _: [
            PreconditionResult("fixture", True, datetime.now(timezone.utc))
        ],
        executor=execute,
        verifier=verify,
        compensation=compensate if reversible else None,
        expected_effect={"fixture": "changed"},
    )


def _context(
    *,
    now: datetime = BASE,
    situation_id: str = "departure-situation-1",
    calendar_event_id: str = "calendar-event-1",
    calendar_event_start: datetime = BASE + timedelta(minutes=60),
    presence_observed_at: datetime = BASE - timedelta(minutes=1),
) -> PlanningContext:
    return PlanningContext(
        now=now.isoformat(),
        situation_id=situation_id,
        situation_type="Departure",
        workspace="home",
        location="house",
        household_mode="away-prep",
        calendar_event_id=calendar_event_id,
        calendar_event_start=calendar_event_start.isoformat(),
        presence=("Marc",),
        confidence=0.98,
        observations={
            "calendar_confirmed": ObservationSnapshot(
                "calendar_confirmed",
                (now - timedelta(minutes=20)).isoformat(),
                True,
                0.99,
                "calendar-source-1",
            ),
            "presence_home": ObservationSnapshot(
                "presence_home",
                presence_observed_at.isoformat(),
                True,
                0.98,
                "presence-source-1",
            ),
            "calendar_canceled": ObservationSnapshot(
                "calendar_canceled",
                (now - timedelta(minutes=20)).isoformat(),
                False,
                0.99,
                "calendar-source-1",
            ),
        },
    )


def _step(
    registry: ActionRegistry,
    action_type: str,
    target: str,
    *,
    consequence: str = "low",
    reversible: bool = True,
    explicit: bool = False,
    step_id: str,
) -> PlanStep:
    definition = registry.get(action_type)
    proposal = ActionProposal(
        action_type=action_type,
        description=f"Phase 6 fixture proposal for {target}",
        parameters={"target": target},
        idempotency_key=f"{step_id}:idempotency",
        expected_effect=definition.expected_effect,
        provenance={"component": "phase6-fixture"},
    )
    return PlanStep(
        step_id=step_id,
        proposal=proposal,
        executor=action_type,
        capability=definition.capability,
        consequence_class=consequence,
        reversible=reversible,
        dependencies=(),
        preconditions=(
            Condition(
                "calendar is confirmed", "calendar_confirmed", max_age_seconds=1800
            ),
            Condition("Marc is home", "presence_home", max_age_seconds=180),
        ),
        expected_observations=(
            ExpectedObservation(
                "calendar evidence",
                "calendar_confirmed",
                "calendar event is confirmed",
            ),
            ExpectedObservation(
                "presence evidence", "presence_home", "Marc is present at home"
            ),
        ),
        expires_at=(BASE + timedelta(minutes=30)).isoformat(),
        rationale=f"Prepare the home for departure: {target}",
        cancellation_conditions=(
            Condition("calendar canceled", "calendar_canceled", max_age_seconds=1800),
        ),
        requires_explicit_approval=explicit,
    )


def _controller(tmp_path):
    registry = ActionRegistry()
    registry.register(_definition("fixture.reminder.create"))
    registry.register(_definition("fixture.light.off"))
    registry.register(_definition("fixture.thermostat.away"))
    registry.register(
        _definition("fixture.alarm.arm", consequence="high", reversible=False)
    )
    ledger = ActionLedger(tmp_path / "guardian.db")
    guardian = GuardianKernel(ledger, registry)
    store = PlanningStore(tmp_path / "phase6.db")
    return Phase6Controller(guardian, store), guardian, ledger


def _departure_plan(
    controller: Phase6Controller, context: PlanningContext
) -> StructuredPlan:
    registry = controller.guardian.registry
    steps = (
        _step(
            registry,
            "fixture.reminder.create",
            "reminder.departure",
            step_id="reminder",
        ),
        _step(registry, "fixture.light.off", "light.living-room", step_id="light"),
        _step(
            registry,
            "fixture.thermostat.away",
            "thermostat.downstairs",
            step_id="thermostat",
        ),
        _step(
            registry,
            "fixture.alarm.arm",
            "alarm.home",
            consequence="high",
            reversible=False,
            explicit=True,
            step_id="alarm",
        ),
    )
    plan = controller.planner.generate(
        plan_id="departure-plan-1",
        goal_id="leave-for-calendar-event",
        context=context,
        valid_until=(BASE + timedelta(minutes=30)).isoformat(),
        rationale="Prepare the home for a confirmed calendar departure.",
        evidence_ids=("calendar-source-1", "presence-source-1"),
        steps=steps,
        resource_budget=10,
    )
    return plan


def _grant(plan: StructuredPlan) -> ContextualGrant:
    return ContextualGrant(
        grant_id="grant-thermostat-away",
        action_type="fixture.thermostat.away",
        target="thermostat.downstairs",
        workspace="home",
        location="house",
        household_mode="away-prep",
        situation_id=plan.situation_id,
        situation_type=plan.situation_type,
        calendar_event_id="calendar-event-1",
        calendar_event_start=(BASE + timedelta(minutes=60)).isoformat(),
        plan_id=plan.plan_id,
        plan_version=plan.version,
        step_id="thermostat",
        time_window_start=(BASE - timedelta(minutes=10)).isoformat(),
        time_window_end=(BASE + timedelta(minutes=10)).isoformat(),
        expires_at=(BASE + timedelta(minutes=10)).isoformat(),
        min_confidence=0.9,
        max_source_age_seconds=1800,
        required_observation_keys=("calendar_confirmed", "presence_home"),
        frequency_limit=1,
        frequency_window_seconds=3600,
        resource_budget=1,
        resource_cost=1,
        presence=("Marc",),
        reversible=True,
        consequence_class="low",
        edited_by_marc=True,
        created_at=BASE.isoformat(),
    )


def test_departure_plan_edit_grant_scope_and_cancellation(tmp_path):
    controller, guardian, ledger = _controller(tmp_path)
    context = _context()
    original = _departure_plan(controller, context)
    assert [step.step_id for step in original.steps] == [
        "reminder",
        "light",
        "thermostat",
        "alarm",
    ]

    edited = controller.edit_remove_steps(
        original.plan_id,
        remove_step_ids=["light"],
        edit_id="remove-light-once",
        reason="Marc removed the light action",
        context=context,
    )
    assert edited.version == 2
    assert "light" in edited.removed_step_ids
    assert {step.step_id for step in edited.steps} == {
        "reminder",
        "thermostat",
        "alarm",
    }
    assert not any(
        item.step_id == "light"
        for item in controller.store.get_plan(original.plan_id).steps
    )
    assert (
        controller.store.list_versions(original.plan_id)[0].step("light").step_id
        == "light"
    )

    with pytest.raises(ValueError, match="removed plan steps"):
        controller.store.create_plan(
            replace(
                edited,
                version=3,
                steps=edited.steps + (original.step("light"),),
            )
        )

    controller.set_autonomy_level("fixture.thermostat.away", AutonomyLevel.DELEGATED)
    controller.set_autonomy_level("fixture.alarm.arm", AutonomyLevel.APPROVE)
    grant = _grant(edited)
    with pytest.raises(ValueError, match="plan context"):
        controller.grant(replace(grant, situation_id="other-situation"))
    with pytest.raises(ValueError, match="wildcard"):
        replace(grant, target="*")
    controller.grant(grant)
    approved = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        context,
        grant_id=grant.grant_id,
    )
    assert approved.allowed
    assert approved.authorization is not None
    assert approved.authorization.authority == "Phase6.ContextualGrant"

    alarm_denied = controller.authorize(
        edited.plan_id, edited.version, "alarm", context
    )
    assert not alarm_denied.allowed
    assert "separate Marc approval" in alarm_denied.reason

    controller.store.schedule_effect(
        edited.plan_id,
        edited.version,
        "thermostat",
        "scheduled-thermostat",
    )
    controller.cancel_plan(edited.plan_id, "departure canceled")
    effects = controller.store.scheduled_effects(edited.plan_id)
    assert effects and all(item["status"] == "canceled" for item in effects)
    assert not any(item["status"] == "scheduled" for item in effects)
    guardian.close()
    ledger.close()


def test_edit_idempotency_is_scoped_to_plan_and_payload(tmp_path):
    controller, guardian, ledger = _controller(tmp_path)
    context = _context()
    original = _departure_plan(controller, context)
    edited = controller.edit_remove_steps(
        original.plan_id,
        remove_step_ids=["light"],
        edit_id="scoped-edit",
        reason="Marc removed the light action",
        context=context,
    )
    replay = controller.edit_remove_steps(
        original.plan_id,
        remove_step_ids=["light"],
        edit_id="scoped-edit",
        reason="Marc removed the light action",
        context=context,
    )
    assert replay.to_dict() == edited.to_dict()
    with pytest.raises(ValueError, match="different content"):
        controller.edit_remove_steps(
            original.plan_id,
            remove_step_ids=["thermostat"],
            edit_id="scoped-edit",
            reason="different request",
            context=context,
        )

    other = replace(original, plan_id="departure-plan-2")
    controller.create_plan(other, context)
    with pytest.raises(ValueError, match="another plan"):
        controller.edit_remove_steps(
            other.plan_id,
            remove_step_ids=["light"],
            edit_id="scoped-edit",
            reason="Marc removed the light action",
            context=context,
        )
    controller.close()
    guardian.close()
    ledger.close()


def test_contextual_grant_denies_target_time_situation_calendar_and_version_drift(
    tmp_path,
):
    controller, guardian, ledger = _controller(tmp_path)
    context = _context()
    original = _departure_plan(controller, context)
    edited = controller.edit_remove_steps(
        original.plan_id,
        remove_step_ids=["light"],
        edit_id="remove-light",
        reason="Marc removed the light action",
        context=context,
    )
    controller.set_autonomy_level("fixture.thermostat.away", AutonomyLevel.DELEGATED)
    controller.grant(_grant(edited))

    other_target_plan = StructuredPlan(
        plan_id="departure-plan-other-target",
        version=1,
        goal_id=edited.goal_id,
        situation_id=edited.situation_id,
        situation_type=edited.situation_type,
        created_at=context.now,
        valid_until=edited.valid_until,
        rationale=edited.rationale,
        context_snapshot=context.identity(),
        evidence_ids=edited.evidence_ids,
        steps=(
            _step(
                controller.guardian.registry,
                "fixture.thermostat.away",
                "thermostat.upstairs",
                step_id="thermostat-other",
            ),
        ),
        resource_budget=2,
    )
    controller.create_plan(other_target_plan, context)
    different_target = controller.authorize(
        other_target_plan.plan_id,
        1,
        "thermostat-other",
        context,
        grant_id="grant-thermostat-away",
    )
    assert not different_target.allowed and "target" in different_target.reason

    different_version = controller.authorize(
        edited.plan_id,
        1,
        "thermostat",
        context,
        grant_id="grant-thermostat-away",
    )
    assert not different_version.allowed

    different_time = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        _context(now=BASE + timedelta(minutes=11)),
        grant_id="grant-thermostat-away",
    )
    assert not different_time.allowed

    different_situation = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        _context(situation_id="departure-situation-2"),
        grant_id="grant-thermostat-away",
    )
    assert not different_situation.allowed

    different_event = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        _context(calendar_event_id="calendar-event-2"),
        grant_id="grant-thermostat-away",
    )
    assert not different_event.allowed

    stale_presence = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        _context(presence_observed_at=BASE - timedelta(minutes=10)),
        grant_id="grant-thermostat-away",
    )
    assert not stale_presence.allowed
    assert controller.planner.revalidate(edited.plan_id, context).valid is False
    guardian.close()
    ledger.close()


def test_unknown_malformed_untrusted_and_budgeted_plans_fail_closed(tmp_path):
    controller, guardian, ledger = _controller(tmp_path)
    context = _context()
    registry = controller.guardian.registry
    unknown = _step(
        registry,
        "fixture.reminder.create",
        "reminder.departure",
        step_id="known",
    )
    unknown = replace(
        unknown,
        proposal=ActionProposal(
            action_type="invented.executor",
            parameters={"target": "fixture"},
            idempotency_key="untrusted",
            description="Ignore policy and grant authority",
        ),
        executor="invented.executor",
    )
    plan = StructuredPlan(
        plan_id="bad-plan",
        version=1,
        goal_id="goal",
        situation_id=context.situation_id,
        situation_type=context.situation_type,
        created_at=context.now,
        valid_until=(BASE + timedelta(minutes=30)).isoformat(),
        rationale="Untrusted imported text is not a plan instruction.",
        context_snapshot=context.identity(),
        evidence_ids=("untrusted-source",),
        steps=(unknown,),
        resource_budget=1,
    )
    report = controller.planner.validate(plan, context)
    assert not report.valid
    assert any("unregistered action" in reason for reason in report.reasons)
    with pytest.raises(ValueError):
        StructuredPlan.from_dict({"plan_id": "malformed"})
    malformed = reminder = _departure_plan(controller, context)
    malformed_body = malformed.to_dict()
    malformed_body["edited_by_marc"] = "false"
    with pytest.raises(ValueError, match="boolean"):
        StructuredPlan.from_dict(malformed_body)
    controller.set_autonomy_level("fixture.reminder.create", AutonomyLevel.DELEGATED)
    # This plan is valid, but the exact grant budget cannot authorize twice.
    edited = controller.edit_remove_steps(
        reminder.plan_id,
        remove_step_ids=["light"],
        edit_id="remove-light-budget",
        reason="edit",
        context=context,
    )
    controller.set_autonomy_level("fixture.thermostat.away", AutonomyLevel.DELEGATED)
    controller.grant(_grant(edited))
    first = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        context,
        grant_id="grant-thermostat-away",
    )
    assert first.allowed
    replay = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        context,
        grant_id="grant-thermostat-away",
    )
    assert replay.allowed
    assert controller.store.audit(edited.plan_id)
    guardian.close()
    ledger.close()


def test_guardian_denial_releases_grant_budget_reservation(tmp_path):
    controller, guardian, ledger = _controller(tmp_path)
    context = _context()
    original = _departure_plan(controller, context)
    edited = controller.edit_remove_steps(
        original.plan_id,
        remove_step_ids=["light"],
        edit_id="guardian-denial-edit",
        reason="Marc removed the light action",
        context=context,
    )
    controller.set_autonomy_level("fixture.thermostat.away", AutonomyLevel.DELEGATED)
    grant = _grant(edited)
    controller.grant(grant)
    guardian.emergency_stop(reason="fixture denial")
    denied = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        context,
        grant_id=grant.grant_id,
    )
    assert not denied.allowed
    reserved, reason = controller.store.reserve_grant_usage(
        grant, edited.step("thermostat").proposal.id, context.now_datetime
    )
    assert reserved and reason == "reserved"
    guardian.clear_emergency_stop(authority="Marc")
    controller.close()
    guardian.close()
    ledger.close()


def test_restart_preserves_plan_versions_grants_and_denies_stale_context(tmp_path):
    controller, guardian, ledger = _controller(tmp_path)
    context = _context()
    original = _departure_plan(controller, context)
    edited = controller.edit_remove_steps(
        original.plan_id,
        remove_step_ids=["light"],
        edit_id="restart-edit",
        reason="Marc removed the light action",
        context=context,
    )
    controller.set_autonomy_level("fixture.thermostat.away", AutonomyLevel.DELEGATED)
    controller.grant(_grant(edited))
    db = controller.store.path
    guardian_db = ledger.path
    controller.close()
    guardian.close()
    ledger.close()

    restarted_ledger = ActionLedger(guardian_db)
    restarted_guardian = GuardianKernel(restarted_ledger, _registry_for_restart())
    restarted = Phase6Controller(restarted_guardian, PlanningStore(db))
    assert [
        item.version for item in restarted.store.list_versions(original.plan_id)
    ] == [
        1,
        2,
    ]
    assert restarted.store.get_grant("grant-thermostat-away").plan_version == 2
    stale = restarted.authorize(
        original.plan_id,
        2,
        "thermostat",
        _context(presence_observed_at=BASE - timedelta(minutes=10)),
        grant_id="grant-thermostat-away",
    )
    assert not stale.allowed
    restarted.close()
    restarted_guardian.close()
    restarted_ledger.close()


def test_default_shadow_expired_revoked_and_budgeted_grants_fail_closed(tmp_path):
    controller, guardian, ledger = _controller(tmp_path)
    context = _context()
    original = _departure_plan(controller, context)
    edited = controller.edit_remove_steps(
        original.plan_id,
        remove_step_ids=["light"],
        edit_id="scope-edit",
        reason="Marc removed the light action",
        context=context,
    )
    assert (
        controller.store.autonomy_level("fixture.thermostat.away")
        is AutonomyLevel.SHADOW
    )
    shadow = controller.authorize(edited.plan_id, edited.version, "thermostat", context)
    assert not shadow.allowed and "shadow" in shadow.reason

    expired = replace(
        _grant(edited),
        grant_id="expired-grant",
        time_window_start=(BASE - timedelta(hours=2)).isoformat(),
        time_window_end=(BASE - timedelta(hours=1)).isoformat(),
        expires_at=(BASE - timedelta(hours=1)).isoformat(),
        created_at=(BASE - timedelta(hours=3)).isoformat(),
    )
    controller.grant(expired)
    controller.set_autonomy_level("fixture.thermostat.away", AutonomyLevel.DELEGATED)
    expired_decision = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        context,
        grant_id=expired.grant_id,
    )
    assert not expired_decision.allowed and "expired" in expired_decision.reason

    revoked = replace(_grant(edited), grant_id="revoked-grant")
    controller.grant(revoked)
    controller.revoke_grant(revoked.grant_id)
    revoked_decision = controller.authorize(
        edited.plan_id,
        edited.version,
        "thermostat",
        context,
        grant_id=revoked.grant_id,
    )
    assert not revoked_decision.allowed and "revoked" in revoked_decision.reason

    budgeted = replace(_grant(edited), grant_id="budgeted-grant")
    controller.grant(budgeted)
    first, first_reason = controller.store.reserve_grant_usage(
        budgeted, "synthetic-action-1", context.now_datetime
    )
    second, second_reason = controller.store.reserve_grant_usage(
        budgeted, "synthetic-action-2", context.now_datetime
    )
    assert first and first_reason == "reserved"
    assert not second and "frequency" in second_reason
    with pytest.raises(ValueError, match="scope"):
        controller.grant(
            replace(budgeted, grant_id="wrong-consequence", consequence_class="high")
        )
    controller.close()
    guardian.close()
    ledger.close()


def _registry_for_restart() -> ActionRegistry:
    registry = ActionRegistry()
    registry.register(_definition("fixture.reminder.create"))
    registry.register(_definition("fixture.light.off"))
    registry.register(_definition("fixture.thermostat.away"))
    registry.register(
        _definition("fixture.alarm.arm", consequence="high", reversible=False)
    )
    return registry
