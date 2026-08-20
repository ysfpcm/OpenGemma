# ruff: noqa: E501
"""Deterministic simulation-first embodiment adapter.

The controller is the only component that compiles an intent into a bounded
actuator command.  Language/VLA/world-model input remains a proposal.  The
simulator registers one ordinary typed action with Guardian; Guardian itself
has no knowledge of Arrival or of embodiment-specific branches.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Optional

from openjarvis.cognition import (
    ActionError,
    ActionProposal,
    ActionRuntime,
    ActionState,
    ExecutionOutcome,
)
from openjarvis.guardian.kernel import (
    ActionDefinition,
    ActionRegistry,
    GuardianKernel,
    PreconditionResult,
)

from .contracts import (
    CausalTimelineEntry,
    CommunicationState,
    CommunicationStatus,
    ControllerDecision,
    ControllerStatus,
    EmbodimentIntent,
    FaultKind,
    PerceptionSnapshot,
    SafetyCheck,
    SafetyEnvelope,
    SafetyStop,
    SimulatedActuatorCommand,
    SimulatedVerification,
    SimulationResult,
    new_id,
)
from .manager import PackInstallError, PackManager
from .store import Phase12Store


class EmbodimentSafetyError(ValueError):
    pass


class LocalActuatorSimulator:
    """Small deterministic virtual actuator state machine; it has no I/O path."""

    STATE_KEY = "virtual-actuators"

    def __init__(self, store: Phase12Store) -> None:
        self.store = store
        self._state = store.load_state(self.STATE_KEY) or {
            "virtual_arm": 0.0,
            "virtual_gripper": 0.0,
            "halted": False,
            "last_command_id": None,
            "sequence": 0,
            "live_effects": False,
        }
        self._persist()

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def apply(self, command: SimulatedActuatorCommand) -> ExecutionOutcome:
        if not command.simulation_only:
            return ExecutionOutcome(
                False, "non-simulation command rejected", ActionError.DENIED
            )
        if self._state.get("halted"):
            return ExecutionOutcome(
                False, "simulated actuator is halted", ActionError.CANCELED
            )
        if self._state.get("last_command_id") == command.command_id:
            return ExecutionOutcome(True, {"duplicate": True, "state": self.state})
        current = float(self._state.get(command.actuator, 0.0))
        if command.operation == "nudge":
            value = max(-1.0, min(1.0, current + command.value))
        elif command.operation == "open":
            value = 1.0
        elif command.operation == "close":
            value = 0.0
        elif command.operation == "hold":
            value = current
        else:
            return ExecutionOutcome(
                False,
                "operation is outside the simulator vocabulary",
                ActionError.INVALID,
            )
        self._state[command.actuator] = value
        self._state["last_command_id"] = command.command_id
        self._state["sequence"] = command.sequence
        self._state["halted"] = False
        self._state["live_effects"] = False
        self._persist()
        return ExecutionOutcome(True, {"state": self.state, "simulation_only": True})

    def observe(self, command: SimulatedActuatorCommand) -> tuple[bool, dict[str, Any]]:
        observed = self.state
        return observed.get(
            "last_command_id"
        ) == command.command_id and not observed.get("halted", False), observed

    def stop(self) -> None:
        self._state["halted"] = True
        self._state["live_effects"] = False
        self._persist()

    def clear_stop(self, *, authority: str) -> None:
        if authority != "Marc":
            raise PermissionError("only Marc may clear the simulated actuator stop")
        self._state["halted"] = False
        self._persist()

    def direct_motor_command(self, *_: Any, **__: Any) -> None:
        raise EmbodimentSafetyError(
            "direct language-model/VLA motor commands are not accepted"
        )

    def _persist(self) -> None:
        self.store.save_state(self.STATE_KEY, self._state)


@dataclass
class _SimulationContext:
    command: SimulatedActuatorCommand
    simulator: LocalActuatorSimulator
    envelope: SafetyEnvelope
    perception: PerceptionSnapshot
    communication: CommunicationState
    fault: Optional[FaultKind] = None

    def preconditions(self, proposal: ActionProposal) -> list[PreconditionResult]:
        now = datetime.now(timezone.utc)
        allowed, checks = self.envelope.check(
            self.command, self.perception, self.communication, now=now.isoformat()
        )
        results = [
            PreconditionResult(
                name=item.name,
                satisfied=item.passed,
                observed_at=now,
                details={"reason": item.reason, "independent_safety_controller": True},
                contradictory=item.name == "non-contradictory-perception"
                and not item.passed,
            )
            for item in checks
        ]
        if self.fault in {
            FaultKind.UNSAFE_MOTION,
            FaultKind.CANCELED,
            FaultKind.CONTROLLER_TIMEOUT,
        }:
            results.append(
                PreconditionResult(
                    "fault-injection", False, now, {"fault": self.fault.value}
                )
            )
        if self.simulator.state.get("halted"):
            results.append(
                PreconditionResult(
                    "simulator-halted", False, now, {"reason": "safety stop"}
                )
            )
        return results

    def execute(self, proposal: ActionProposal) -> ExecutionOutcome:
        return self.simulator.apply(self.command)

    def verify(self, proposal: ActionProposal) -> tuple[bool, Any]:
        return self.simulator.observe(self.command)


class _SimulationActionRouter:
    """Routes generic action callbacks to a controller-owned durable context."""

    def __init__(self) -> None:
        self.contexts: dict[str, _SimulationContext] = {}

    def bind(self, action_id: str, context: _SimulationContext) -> None:
        self.contexts[action_id] = context

    def context(self, proposal: ActionProposal) -> _SimulationContext:
        try:
            return self.contexts[proposal.id]
        except KeyError as exc:
            raise EmbodimentSafetyError(
                "no durable controller context is bound"
            ) from exc


def register_simulated_actuator_action(
    registry: ActionRegistry,
) -> _SimulationActionRouter:
    """Register the generic simulator adapter exactly once per action registry."""

    existing = getattr(registry, "_phase12_simulation_router", None)
    if existing is not None:
        return existing
    router = _SimulationActionRouter()
    definition = ActionDefinition(
        action_type="embodiment.simulated_actuator",
        input_schema={
            "type": "object",
            "required": ["command_id", "actuator", "operation", "target", "value"],
            "properties": {
                "command_id": {"type": "string"},
                "actuator": {"type": "string"},
                "operation": {"type": "string"},
                "target": {"type": "string"},
                "value": {"type": "number"},
            },
            "additionalProperties": False,
        },
        capability="embodiment:simulate",
        risk_class="low",
        consequence_class="low",
        preconditions=lambda proposal: router.context(proposal).preconditions(proposal),
        executor=lambda proposal: router.context(proposal).execute(proposal),
        verifier=lambda proposal: router.context(proposal).verify(proposal),
        freshness_seconds=2,
        idempotency_strategy="intent-idempotency-key",
        expected_effect={"simulation_only": True},
        redaction_policy="metadata-only",
    )
    registry.register(definition)
    setattr(registry, "_phase12_simulation_router", router)
    return router


class DeterministicEmbodimentController:
    """Compile proposal-only intents into Guardian-mediated virtual effects."""

    def __init__(
        self,
        store: Phase12Store,
        pack_manager: PackManager,
        guardian: GuardianKernel,
        *,
        pack_id: str = "digital-embodiment",
        controller_id: str = "phase12-deterministic-controller",
        guardian_grant_id: Optional[str] = None,
        envelope: Optional[SafetyEnvelope] = None,
        simulator: Optional[LocalActuatorSimulator] = None,
    ) -> None:
        self.store = store
        self.pack_manager = pack_manager
        self.guardian = guardian
        self.pack_id = pack_id
        self.controller_id = controller_id
        self.guardian_grant_id = guardian_grant_id
        self.envelope = envelope or SafetyEnvelope("phase12-safety-envelope")
        self.simulator = simulator or LocalActuatorSimulator(store)
        self.router = register_simulated_actuator_action(guardian.registry)
        self._authorizations: dict[str, Any] = {}

    def submit(
        self,
        intent: EmbodimentIntent,
        perception: PerceptionSnapshot,
        communication: CommunicationState,
        *,
        fault: FaultKind | None = None,
        crash_after_authorization: bool = False,
    ) -> SimulationResult:
        if communication.status is not CommunicationStatus.CONNECTED and fault is None:
            fault = FaultKind.COMMUNICATION_LOSS
        if fault is FaultKind.COMMUNICATION_LOSS:
            communication = replace(
                communication,
                status=CommunicationStatus.LOST,
                reason="communication-loss fault",
            )
        self.store.save_communication(communication)
        first_delivery = self.store.save_intent(intent)
        if not first_delivery:
            return self._duplicate_result(intent, perception, communication)
        command = self._compile_command(intent)
        self.store.save_command(command, status="candidate")
        installation = self.pack_manager.installation(self.pack_id)
        if intent.pack_id != self.pack_id:
            return self._refused(
                intent,
                command,
                ControllerStatus.REFUSED,
                "intent pack identity does not match the controller pack",
                (),
            )
        if (
            installation is not None
            and intent.installation_id != installation.installation_id
        ):
            return self._refused(
                intent,
                command,
                ControllerStatus.REFUSED,
                "intent installation identity is outside the live installation grant",
                (),
            )
        context = _SimulationContext(
            command, self.simulator, self.envelope, perception, communication, fault
        )
        safe, checks = self.envelope.check(command, perception, communication)
        if fault in {
            FaultKind.UNSAFE_MOTION,
            FaultKind.STALE_PERCEPTION,
            FaultKind.CONTRADICTORY_PERCEPTION,
            FaultKind.CANCELED,
            FaultKind.REVOKED_AUTHORITY,
            FaultKind.EMERGENCY_STOP,
            FaultKind.DUPLICATE_COMMAND,
        }:
            safe = False
        if fault is FaultKind.CONTROLLER_TIMEOUT:
            return self._refused(
                intent,
                command,
                ControllerStatus.TIMED_OUT,
                "deterministic controller timeout; no actuator command was dispatched",
                checks,
            )
        try:
            if (
                installation is None
                or installation.state.value != "installed"
                or installation.authority_scope.revoked
            ):
                return self._refused(
                    intent,
                    command,
                    ControllerStatus.REFUSED,
                    "pack installation is revoked or unavailable",
                    checks,
                )
            if not safe:
                reason = next(
                    (item.reason for item in checks if not item.passed),
                    "independent safety envelope refused the command",
                )
                if fault:
                    reason = f"{fault.value}: {reason}"
                status = (
                    ControllerStatus.STOPPED
                    if fault is FaultKind.EMERGENCY_STOP
                    else ControllerStatus.REFUSED
                )
                return self._refused(intent, command, status, reason, checks)
            policy_proposal = self.pack_manager.build_proposal(
                self.pack_id,
                action_type="embodiment.simulated_actuator",
                parameters={
                    "command_id": command.command_id,
                    "actuator": command.actuator,
                    "operation": command.operation,
                    "target": command.target,
                    "value": command.value,
                },
                plan_id=f"plan:{intent.intent_id}",
                idempotency_key=intent.idempotency_key,
                expected_effect={
                    "command_id": command.command_id,
                    "simulation_only": True,
                },
            )
            proposal = ActionProposal(
                id=policy_proposal.proposal_id,
                action_type=policy_proposal.action_type,
                description="Deterministic simulated actuator proposal",
                parameters=policy_proposal.parameters,
                idempotency_key=policy_proposal.idempotency_key,
                expected_effect=policy_proposal.expected_effect,
                causal_parents=[intent.intent_id, policy_proposal.plan_id],
                provenance={
                    "component": self.controller_id,
                    "pack_id": self.pack_id,
                    "simulation_only": True,
                },
            )
            self.router.bind(proposal.id, context)
            guardian_decision = self.guardian.authorize(
                proposal,
                session_id=installation.installation_id,
                authority="Phase12 deterministic controller",
            )
            if not guardian_decision.allowed:
                return self._refused(
                    intent,
                    command,
                    ControllerStatus.REFUSED,
                    guardian_decision.reason
                    or "Guardian denied the exact installation-scoped action",
                    checks,
                    action_id=proposal.id,
                    authorization_id=guardian_decision.authorization.id,
                )
            decision = ControllerDecision(
                decision_id=new_id("controller-decision"),
                intent_id=intent.intent_id,
                command_id=command.command_id,
                status=ControllerStatus.ACCEPTED,
                reason="Guardian authorized the exact installation-scoped simulation action",
                safety_checks=checks,
                action_id=proposal.id,
                authorization_id=guardian_decision.authorization.id,
                causal_parents=(
                    intent.intent_id,
                    policy_proposal.plan_id,
                    guardian_decision.authorization.id,
                ),
                live_effects=False,
            )
            self._authorizations[proposal.id] = guardian_decision.authorization
            self.store.save_decision(decision)
            if crash_after_authorization:
                return self._result(decision, None)
            return self._execute_and_record(decision, context)
        except PackInstallError as exc:
            return self._refused(
                intent, command, ControllerStatus.REFUSED, str(exc), checks
            )

    def resume(
        self,
        action_id: str,
        perception: PerceptionSnapshot,
        communication: CommunicationState,
    ) -> SimulationResult:
        decision = next(
            (item for item in self.store.decisions() if item.action_id == action_id),
            None,
        )
        if decision is None:
            raise KeyError(action_id)
        command = self.store.command_by_id(decision.command_id)
        if command is None:
            raise KeyError(decision.command_id)
        stored_intent = self.store.get_intent_by_key(command.idempotency_key)
        if stored_intent is None:
            raise KeyError(command.idempotency_key)
        installation = self.pack_manager.installation(self.pack_id)
        if self.guardian.ledger.state(action_id) is ActionState.AUTHORIZED and (
            installation is None
            or installation.state.value != "installed"
            or installation.authority_scope.revoked
            or stored_intent.installation_id != installation.installation_id
        ):
            self.guardian.ledger.transition(
                action_id,
                ActionState.FAILED,
                "pack-installation-revoked-before-resume",
                error=ActionError.DENIED,
                details={"pack_id": self.pack_id},
            )
            updated = replace(
                decision,
                status=ControllerStatus.REFUSED,
                reason="pack installation authority was revoked before execution; no actuator command was sent",
            )
            self.store.save_decision(updated)
            return self._result(updated, None)
        self.store.save_communication(communication)
        proposal = self.guardian.ledger.proposal(action_id)
        context = _SimulationContext(
            command, self.simulator, self.envelope, perception, communication
        )
        self.router.bind(action_id, context)
        state = self.guardian.ledger.state(action_id)
        if state is ActionState.AUTHORIZED:
            return self._execute_and_record(decision, context)
        if state is ActionState.EFFECT_PENDING:
            verification = ActionRuntime(self.guardian.ledger).verify(
                action_id,
                lambda: context.verify(proposal),
                verifier_name="independent-simulator-observer",
            )
            return self._record_verification(decision, verification, context)
        return self._result(
            replace(
                decision,
                status=ControllerStatus.DUPLICATE,
                reason="restart found a terminal action; no duplicate command was sent",
            ),
            self.store.get_verification(action_id),
        )

    def emergency_stop(self, *, reason: str = "Phase 12 emergency stop") -> SafetyStop:
        self.envelope = replace(self.envelope, emergency_stop_active=True)
        self.simulator.stop()
        self.guardian.emergency_stop(reason=reason)
        stop = SafetyStop(
            new_id("safety-stop"),
            FaultKind.EMERGENCY_STOP.value,
            reason,
            None,
            emergency=True,
        )
        self.store.save_safety_stop(stop)
        return stop

    def clear_emergency_stop(self, *, authority: str) -> None:
        self.guardian.clear_emergency_stop(authority=authority)
        self.simulator.clear_stop(authority=authority)
        self.envelope = replace(self.envelope, emergency_stop_active=False)

    def _compile_command(self, intent: EmbodimentIntent) -> SimulatedActuatorCommand:
        operation = str(intent.parameters.get("operation", "nudge"))
        value = float(intent.parameters.get("value", 0.0))
        actuator = str(intent.parameters.get("actuator", "virtual_arm"))
        return SimulatedActuatorCommand(
            command_id=f"command:{intent.intent_id}",
            intent_id=intent.intent_id,
            actuator=actuator,
            operation=operation,
            target=intent.target,
            value=value,
            sequence=int(self.simulator.state.get("sequence", 0)) + 1,
            idempotency_key=intent.idempotency_key,
            controller_id=self.controller_id,
        )

    def _execute_and_record(
        self, decision: ControllerDecision, context: _SimulationContext
    ) -> SimulationResult:
        result = self.guardian.execute(
            decision.action_id or "", self._authorization(decision)
        )
        attempts = self.guardian.ledger.attempts(decision.action_id or "")
        if attempts:
            attempt = attempts[-1]
            self.store.save_actuator_attempt(
                decision.action_id or "",
                attempt.id,
                attempt.to_dict(),
            )
        if result.verification is not None:
            return self._record_verification(decision, result.verification, context)
        status = (
            ControllerStatus.STOPPED
            if result.state in {ActionState.FAILED, ActionState.NEEDS_ATTENTION}
            else ControllerStatus.REFUSED
        )
        updated = replace(
            decision,
            status=status,
            reason=result.exception_summary or result.outcome.response,
        )
        self.store.save_decision(updated)
        return self._result(updated, None)

    def _authorization(self, decision: ControllerDecision):
        from openjarvis.cognition import Authorization

        if decision.action_id in self._authorizations:
            return self._authorizations[decision.action_id]
        installation = self.pack_manager.installation(self.pack_id)
        command = self.store.command_by_id(decision.command_id)
        return Authorization(
            id=decision.authorization_id or new_id("authorization"),
            action_id=decision.action_id or "",
            decision="authorized",
            authority="Phase12 deterministic controller",
            scope={
                "grant_id": self.guardian_grant_id
                or (installation.authority_scope.grant_id if installation else ""),
                "session_id": installation.installation_id if installation else "",
                "capability": "embodiment:simulate",
                "action_type": "embodiment.simulated_actuator",
                "target": command.target if command else "",
            },
        )

    def _record_verification(
        self,
        decision: ControllerDecision,
        verification: Any,
        context: _SimulationContext,
    ) -> SimulationResult:
        observed = (
            verification.observed_effect
            if isinstance(verification.observed_effect, dict)
            else {"observed": verification.observed_effect}
        )
        simulated = SimulatedVerification(
            verification_id=verification.id,
            action_id=verification.action_id,
            command_id=context.command.command_id,
            expected_state={"last_command_id": context.command.command_id},
            observed_state=observed,
            succeeded=bool(verification.succeeded),
        )
        self.store.save_verification(simulated)
        status = (
            ControllerStatus.ACCEPTED
            if verification.succeeded
            else ControllerStatus.STOPPED
        )
        updated = replace(
            decision,
            status=status,
            reason="independently verified in the local simulator"
            if verification.succeeded
            else "independent simulator verification failed",
        )
        self.store.save_decision(updated)
        return self._result(updated, simulated)

    def _refused(
        self,
        intent: EmbodimentIntent,
        command: SimulatedActuatorCommand,
        status: ControllerStatus,
        reason: str,
        checks: tuple[SafetyCheck, ...],
        *,
        action_id: Optional[str] = None,
        authorization_id: Optional[str] = None,
    ) -> SimulationResult:
        decision = ControllerDecision(
            decision_id=new_id("controller-decision"),
            intent_id=intent.intent_id,
            command_id=command.command_id,
            status=status,
            reason=reason,
            safety_checks=checks,
            action_id=action_id,
            authorization_id=authorization_id,
            causal_parents=(intent.intent_id,),
            live_effects=False,
        )
        self.store.save_decision(decision)
        return self._result(decision, None)

    def _duplicate_result(
        self,
        intent: EmbodimentIntent,
        perception: PerceptionSnapshot,
        communication: CommunicationState,
    ) -> SimulationResult:
        decisions = self.store.decisions(intent.intent_id)
        if not decisions:
            command = self.store.command_by_key(intent.idempotency_key)
            if command is None:
                raise KeyError(intent.idempotency_key)
            return self._refused(
                intent,
                command,
                ControllerStatus.DUPLICATE,
                "duplicate intent has no prior decision; no command was dispatched",
                (),
            )
        prior = decisions[-1]
        duplicate = replace(
            prior,
            decision_id=new_id("controller-duplicate"),
            status=ControllerStatus.DUPLICATE,
            reason="duplicate intent delivery suppressed; no second actuator command",
        )
        self.store.save_decision(duplicate)
        return self._result(
            duplicate, self.store.get_verification(prior.action_id or "")
        )

    def _result(
        self,
        decision: ControllerDecision,
        verification: Optional[SimulatedVerification],
    ) -> SimulationResult:
        timeline = (
            CausalTimelineEntry(
                "Plan",
                decision.causal_parents[1]
                if len(decision.causal_parents) > 1
                else decision.intent_id,
                "proposed",
                "Deterministic controller compiled a bounded intent.",
            ),
            CausalTimelineEntry(
                "Authorization",
                decision.authorization_id or decision.decision_id,
                "authorized" if decision.authorization_id else "denied",
                decision.reason,
                (decision.intent_id,),
            ),
            CausalTimelineEntry(
                "ActionAttempt",
                decision.action_id or decision.command_id,
                "attempted"
                if decision.action_id and decision.status is ControllerStatus.ACCEPTED
                else "not-attempted",
                "Virtual actuator only; no live effect.",
                (decision.authorization_id or decision.intent_id,),
            ),
            CausalTimelineEntry(
                "Verification",
                verification.verification_id if verification else decision.command_id,
                "verified"
                if verification and verification.succeeded
                else "not-verified",
                "Independent local simulator observation.",
                (decision.action_id or decision.command_id,),
            ),
        )
        return SimulationResult(
            intent_id=decision.intent_id,
            status=decision.status,
            decision=decision,
            verification=verification,
            action_id=decision.action_id,
            state=self.simulator.state,
            live_effects=False,
            timeline=timeline,
        )


SimulatedActuator = LocalActuatorSimulator
EmbodimentController = DeterministicEmbodimentController
