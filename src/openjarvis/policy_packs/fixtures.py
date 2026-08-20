"""Deterministic local Phase 12 replay and fault fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .contracts import (
    CommunicationState,
    CommunicationStatus,
    EmbodimentIntent,
    FaultKind,
    PerceptionSnapshot,
    PolicyEvent,
    PolicyEvidence,
)


def _time(minutes_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def arrival_replay_fixtures() -> tuple[PolicyEvent, ...]:
    def evidence(
        prefix: str, *, minutes_ago: int = 0, contradictory: bool = False
    ) -> tuple[PolicyEvidence, ...]:
        observed = _time(minutes_ago)
        return (
            PolicyEvidence(
                f"{prefix}-presence",
                "presence",
                observed,
                confidence=0.95,
                contradictory=contradictory,
                provenance={"fixture": "deterministic-local", "source": "presence"},
                summary="presence classification",
            ),
            PolicyEvidence(
                f"{prefix}-calendar",
                "calendar",
                observed,
                confidence=0.9,
                provenance={"fixture": "deterministic-local", "source": "calendar"},
                summary="calendar context",
            ),
            PolicyEvidence(
                f"{prefix}-connectivity",
                "connectivity",
                observed,
                confidence=0.95,
                provenance={"fixture": "deterministic-local", "source": "connectivity"},
                summary="source health",
            ),
        )

    return tuple(
        PolicyEvent(
            event_id=f"phase12-{scenario}",
            scenario=scenario,
            observed_at=_time(),
            sources=("presence", "calendar", "connectivity"),
            evidence=evidence(scenario, contradictory=scenario == "false-presence"),
            dedupe_key=f"phase12:{scenario}",
            metadata={"fixture": "deterministic-local", "simulation_only": True},
        )
        for scenario in (
            "arrival",
            "guest-present",
            "vacation",
            "false-presence",
            "service-outage",
        )
    )


def embodiment_intent(
    intent_id: str,
    *,
    value: float = 0.1,
    actuator: str = "virtual_arm",
    operation: str = "nudge",
    target: str = "virtual-actuator",
) -> EmbodimentIntent:
    return EmbodimentIntent(
        intent_id=intent_id,
        pack_id="digital-embodiment",
        installation_id="digital-install-1",
        action_type="embodiment.simulated_actuator",
        target=target,
        parameters={"actuator": actuator, "operation": operation, "value": value},
        source="world-model-proposal",
        proposal_only=True,
        confidence=0.95,
        perception_id=f"perception:{intent_id}",
    )


def perception(
    perception_id: str, *, age_seconds: int = 0, contradictory: bool = False
) -> PerceptionSnapshot:
    observed = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return PerceptionSnapshot(
        perception_id,
        observed.isoformat(),
        {"virtual_arm": 0.0},
        contradictory=contradictory,
    )


def communication(
    connection_id: str = "phase12-connection", *, lost: bool = False
) -> CommunicationState:
    return CommunicationState(
        connection_id,
        CommunicationStatus.LOST if lost else CommunicationStatus.CONNECTED,
        reason="fixture" if not lost else "fixture communication loss",
    )


def embodiment_fault_matrix() -> tuple[FaultKind, ...]:
    return (
        FaultKind.UNSAFE_MOTION,
        FaultKind.STALE_PERCEPTION,
        FaultKind.CONTRADICTORY_PERCEPTION,
        FaultKind.COMMUNICATION_LOSS,
        FaultKind.CONTROLLER_TIMEOUT,
        FaultKind.REVOKED_AUTHORITY,
        FaultKind.EMERGENCY_STOP,
        FaultKind.DUPLICATE_COMMAND,
    )
