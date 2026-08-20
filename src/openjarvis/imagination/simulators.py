"""Deterministic Phase 9 simulation layers."""

# ruff: noqa: E501

from __future__ import annotations

from typing import Any

from .fixtures import DepartureFixture, departure_twin
from .models import (
    CandidatePlan,
    SimulationKind,
    SimulationRun,
    SimulationStatus,
    stable_id,
)


class DepartureSimulationSuite:
    """Run all non-live departure simulations against a deterministic fixture."""

    def __init__(self, fixture: DepartureFixture) -> None:
        self.fixture = fixture

    def run_symbolic(self, candidate: CandidatePlan) -> SimulationRun:
        findings: list[str] = []
        outcome = "success"
        action_ids = {action.action_id for action in candidate.actions}
        for action in candidate.actions:
            if not set(action.dependencies) <= action_ids:
                outcome = "failure"
                findings.append("dependency graph contains an unknown prerequisite")
        if outcome == "success":
            dependencies = {
                action.action_id: set(action.dependencies)
                for action in candidate.actions
            }
            visiting: set[str] = set()
            visited: set[str] = set()

            def has_cycle(action_id: str) -> bool:
                if action_id in visiting:
                    return True
                if action_id in visited:
                    return False
                visiting.add(action_id)
                if any(has_cycle(dependency) for dependency in dependencies[action_id]):
                    return True
                visiting.remove(action_id)
                visited.add(action_id)
                return False

            if any(has_cycle(action_id) for action_id in dependencies):
                outcome = "failure"
                findings.append("dependency graph contains a cycle")
        if len(candidate.actions) > 4:
            outcome = "failure"
            findings.append(
                "candidate exceeds the deterministic departure action budget"
            )
        if not self.fixture.traffic_available:
            findings.append("traffic service unavailable; fallback buffer is required")
        return self._run(SimulationKind.SYMBOLIC, candidate, outcome, findings)

    def run_event_replay(self, candidate: CandidatePlan) -> SimulationRun:
        findings: list[str] = []
        outcome = "success"
        if self.fixture.calendar_address and self.fixture.calendar_likely_remote:
            findings.append("calendar address is present but event is likely remote")
            if "physical_attendance" in {
                item.key for item in candidate.expected_observations
            }:
                findings.append(
                    "physical attendance remains unknown; address is not proof"
                )
        return self._run(
            SimulationKind.PERSONAL_EVENT_REPLAY, candidate, outcome, findings
        )

    def run_service_dry_run(self, candidate: CandidatePlan) -> SimulationRun:
        findings: list[str] = []
        outcome = "success"
        if not self.fixture.traffic_available:
            findings.append("traffic API outage injected")
            if "traffic-strict" in candidate.diversity_signature:
                outcome = "failure"
                findings.append("strict traffic dependency cannot be satisfied")
            else:
                findings.append(
                    f"using deterministic {self.fixture.traffic_fallback_minutes}-minute fallback buffer"
                )
        return self._run(SimulationKind.SERVICE_DRY_RUN, candidate, outcome, findings)

    def run_home_assistant_twin(self, candidate: CandidatePlan) -> SimulationRun:
        twin = departure_twin(self.fixture)
        findings: list[str] = []
        outcome = "success"
        for action in candidate.actions:
            if action.action_type == "home_assistant.read_state":
                twin.read_state(action.target)
                continue
            twin.apply(action.action_type, action.target, dict(action.parameters))
            expected = next(
                (
                    item
                    for item in candidate.expected_observations
                    if item.observation_id in action.expected_observation_ids
                ),
                None,
            )
            if expected is not None:
                verified, observed = twin.verify(
                    action.action_type, action.target, dict(action.parameters)
                )
                if not verified:
                    outcome = "failure"
                    findings.append(
                        f"{expected.key}: simulated command reported success but observed state contradicted it"
                    )
                    findings.append(
                        f"digital twin observed {observed.get('state', 'unknown')} for {action.target}"
                    )
        return self._run(
            SimulationKind.HOME_ASSISTANT_TWIN, candidate, outcome, findings
        )

    def all_runs(self, candidate: CandidatePlan) -> list[SimulationRun]:
        return [
            self.run_symbolic(candidate),
            self.run_event_replay(candidate),
            self.run_service_dry_run(candidate),
            self.run_home_assistant_twin(candidate),
        ]

    def _run(
        self,
        kind: SimulationKind,
        candidate: CandidatePlan,
        outcome: str,
        findings: list[str],
    ) -> SimulationRun:
        return SimulationRun(
            simulation_id=stable_id("sim", candidate.candidate_id, kind.value),
            candidate_id=candidate.candidate_id,
            kind=kind,
            status=SimulationStatus.COMPLETED,
            simulation_outcome=outcome,
            evidence_scope="simulation",
            findings=tuple(findings),
            prediction_ids=tuple(
                stable_id("prediction", candidate.candidate_id, item.observation_id)
                for item in candidate.expected_observations
            ),
            assumptions=candidate.assumptions,
            failure_summary=(
                "simulation found a counterfactual failure; no real-world result was produced"
                if outcome == "failure"
                else None
            ),
        )


def candidate_diversity(candidates: list[CandidatePlan]) -> dict[str, Any]:
    signatures = [tuple(candidate.diversity_signature) for candidate in candidates]
    action_shapes = [
        tuple(
            sorted((action.action_type, action.target) for action in candidate.actions)
        )
        for candidate in candidates
    ]
    families = {candidate.strategy_family for candidate in candidates}
    materially_different = len(set(signatures)) == len(candidates) and len(
        set(action_shapes)
    ) == len(candidates)
    return {
        "candidate_count": len(candidates),
        "strategy_families": sorted(families),
        "signatures": [list(item) for item in signatures],
        "action_shapes": [list(item) for item in action_shapes],
        "materially_different": materially_different,
    }


__all__ = ["DepartureSimulationSuite", "candidate_diversity"]
