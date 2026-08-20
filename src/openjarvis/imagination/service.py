"""Phase 9 deliberation, critic, scoring, replay, and Guardian boundary."""

# ruff: noqa: E501

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from openjarvis.cognition import ActionProposal

from .fixtures import DepartureFixture
from .models import (
    CalibrationRecord,
    CandidatePlan,
    CandidateStatus,
    CriticKind,
    CriticResult,
    ExpectedObservation,
    FailureMode,
    ImaginationDecision,
    ObservationRecord,
    Prediction,
    PredictionStatus,
    Score,
    ScoreDimension,
    TypedAction,
    stable_id,
    utc_now,
)
from .simulators import DepartureSimulationSuite, candidate_diversity
from .store import ImaginationStore


@dataclass(frozen=True, slots=True)
class GuardianRevalidation:
    candidate_id: str
    allowed: bool
    proposal_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    authority_created: bool = False
    executed: bool = False


class GuardianBoundary:
    """The only Phase 9 exit: compile and revalidate, never authorize or run."""

    def revalidate(
        self, candidate: CandidatePlan, *, registry: Any = None
    ) -> GuardianRevalidation:
        if candidate.status not in {CandidateStatus.VALID, CandidateStatus.MODIFIED}:
            return GuardianRevalidation(
                candidate.candidate_id,
                False,
                (),
                ("candidate was rejected before Guardian",),
            )
        proposals: list[ActionProposal] = []
        reasons: list[str] = []
        for action in candidate.actions:
            proposal = ActionProposal(
                id=stable_id("proposal", candidate.candidate_id, action.action_id),
                action_type=action.action_type,
                description=action.rationale or candidate.title,
                parameters={"target": action.target, **dict(action.parameters)},
                idempotency_key=stable_id(
                    "idempotency", candidate.candidate_id, action.action_id
                ),
                expected_effect={
                    "candidate_id": candidate.candidate_id,
                    "expected_observation_ids": list(action.expected_observation_ids),
                    "simulation_only": True,
                },
                causal_parents=[candidate.candidate_id],
                provenance={
                    "component": "phase9-guardian-boundary",
                    "simulation_only": True,
                },
            )
            proposals.append(proposal)
            if registry is not None:
                error = registry.validate(proposal)
                if error:
                    reasons.append(f"{action.action_id}: {error}")
        return GuardianRevalidation(
            candidate_id=candidate.candidate_id,
            allowed=not reasons,
            proposal_ids=tuple(item.id for item in proposals),
            reasons=tuple(reasons),
        )


class ImaginationService:
    """Deterministic Phase 9 service used by acceptance and replay lanes."""

    def __init__(self, store: ImaginationStore) -> None:
        self.store = store

    def deliberate_departure(self, fixture: DepartureFixture) -> ImaginationDecision:
        self._seed_failure_modes()
        candidates = self._departure_candidates(fixture)
        diversity = candidate_diversity(candidates)
        if not diversity["materially_different"] or diversity["candidate_count"] != 3:
            raise ValueError(
                "departure deliberation requires three materially different candidates"
            )

        suite = DepartureSimulationSuite(fixture)
        final_candidates: list[CandidatePlan] = []
        all_critic_ids: list[str] = []
        all_score_ids: list[str] = []
        failure_ids: list[str] = []
        prediction_ids: list[str] = []

        for candidate in candidates:
            self.store.save_candidate(candidate)
            original_runs = suite.all_runs(candidate)
            for run in original_runs:
                self.store.save_simulation(run)

            modified = self._safe_modification(candidate, fixture)
            if modified is not None:
                candidate = modified
                self.store.save_candidate(candidate)
                for run in suite.all_runs(candidate):
                    self.store.save_simulation(run)

            critics = self._critics(candidate, fixture)
            for critic in critics:
                self.store.save_critic(critic)
                all_critic_ids.append(critic.critic_id)
                if not critic.passed:
                    failure_ids.extend(critic.evidence_ids)
            status = (
                CandidateStatus.VALID
                if all(item.passed for item in critics)
                else CandidateStatus.REJECTED
            )
            reasons = tuple(
                finding
                for critic in critics
                if not critic.passed
                for finding in critic.findings
            )
            candidate = replace(
                candidate,
                status=status,
                rejection_reasons=reasons,
                updated_at=utc_now(),
            )
            self.store.save_candidate(candidate)

            for expected in candidate.expected_observations:
                prediction = Prediction(
                    prediction_id=stable_id(
                        "prediction", candidate.candidate_id, expected.observation_id
                    ),
                    candidate_id=candidate.candidate_id,
                    observation_id=expected.observation_id,
                    claim=expected.description,
                    expected_value=expected.expected_value,
                    confidence=expected.confidence,
                    provenance={
                        "component": "phase9-imagination",
                        "source": expected.observable_by,
                        "scenario_id": fixture.scenario_id,
                        "simulation_only": True,
                    },
                    assumptions=expected.assumptions,
                    causal_parents=(candidate.candidate_id, *expected.causal_parents),
                    status=PredictionStatus.SIMULATED,
                    simulation_run_id=stable_id(
                        "sim", candidate.candidate_id, "home_assistant_digital_twin"
                    ),
                )
                self.store.save_prediction(prediction)
                prediction_ids.append(prediction.prediction_id)

            score = self._score(candidate, critics)
            self.store.save_score(score)
            all_score_ids.append(score.score_id)
            final_candidates.append(candidate)

        valid = [
            item for item in final_candidates if item.status is CandidateStatus.VALID
        ]
        selected = (
            max(
                valid,
                key=lambda item: (
                    self.store.list_scores(item.candidate_id)[-1].weighted_total
                ),
            )
            if valid
            else None
        )
        reason = self._decision_reason(selected, final_candidates)
        decision = ImaginationDecision(
            decision_id=stable_id("decision", fixture.scenario_id),
            scenario_id=fixture.scenario_id,
            candidate_ids=tuple(item.candidate_id for item in final_candidates),
            valid_candidate_ids=tuple(item.candidate_id for item in valid),
            rejected_candidate_ids=tuple(
                item.candidate_id
                for item in final_candidates
                if item.status is CandidateStatus.REJECTED
            ),
            selected_candidate_id=selected.candidate_id if selected else None,
            reason=reason,
            predicted_observations=tuple(prediction_ids),
            failure_mode_ids=tuple(sorted(set(failure_ids))),
            critic_ids=tuple(all_critic_ids),
            score_ids=tuple(all_score_ids),
            guardian_candidate_ids=tuple(item.candidate_id for item in valid),
        )
        self.store.save_decision(decision)
        return decision

    def record_replayed_observation(
        self,
        prediction_id: str,
        observed_value: Any,
        *,
        source_id: str,
        evidence_scope: str = "replay",
        details: dict[str, Any] | None = None,
    ) -> CalibrationRecord:
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("replayed observations require a non-empty source ID")
        if evidence_scope != "replay":
            raise ValueError(
                "record_replayed_observation only accepts replay evidence scope"
            )
        prediction = self.store.get_prediction(prediction_id)
        observation_id = stable_id("observed", prediction_id, source_id, observed_value)
        calibration_id = stable_id("calibration", prediction_id, observation_id)
        try:
            return self.store.get_calibration(calibration_id)
        except KeyError:
            pass
        if observed_value is None or observed_value == "unknown":
            status = PredictionStatus.UNKNOWN
            error = 1.0
        elif _values_equal(observed_value, prediction.expected_value):
            status = PredictionStatus.VERIFIED
            error = 0.0
        else:
            status = PredictionStatus.CONTRADICTED
            error = 1.0
        observed = ObservationRecord(
            observation_id=observation_id,
            prediction_id=prediction_id,
            observed_value=observed_value,
            source_id=source_id,
            observed_at=utc_now(),
            status=status,
            evidence_scope=evidence_scope,
            provenance={"component": "phase9-replay", "source_id": source_id},
            details=details or {},
        )
        self.store.save_observation(observed)
        self.store.update_prediction_status(
            prediction_id, status, evidence_ids=(observation_id,)
        )
        calibration = CalibrationRecord(
            calibration_id=calibration_id,
            prediction_id=prediction_id,
            expected_value=prediction.expected_value,
            observed_value=observed_value,
            result=status,
            calibration_error=error,
            evidence_ids=(observed.observation_id,),
        )
        self.store.save_calibration(calibration)
        return calibration

    def guardian_handoff(
        self, decision_id: str, *, registry: Any = None
    ) -> list[GuardianRevalidation]:
        decision = self.store.get_decision(decision_id)
        return [
            GuardianBoundary().revalidate(
                self.store.get_candidate(candidate_id), registry=registry
            )
            for candidate_id in decision.guardian_candidate_ids
        ]

    def _seed_failure_modes(self) -> None:
        modes = (
            FailureMode(
                failure_mode_id="failure:cover-feedback-contradiction",
                name="cover-feedback-contradiction",
                description="A cover command reports success while position feedback remains unchanged.",
                trigger="reported_state != observed_state",
                evidence=(
                    "fixture:cover-reported-success",
                    "fixture:cover-state-unchanged",
                ),
                severity="high",
                mitigations=(
                    "read state before acting",
                    "require independent verification",
                    "do not retry automatically",
                ),
                source_episode_ids=("episode:departure-cover-failure",),
            ),
            FailureMode(
                failure_mode_id="failure:household-presence",
                name="another-household-member-present",
                description="A departure automation could inconvenience a person who remains home.",
                trigger="household_present contains someone other than Marc",
                evidence=("fixture:presence-guest",),
                severity="high",
                mitigations=(
                    "do not arm alarm",
                    "avoid away-wide HVAC or lighting changes",
                    "ask for confirmation",
                ),
                source_episode_ids=("episode:departure-presence",),
            ),
            FailureMode(
                failure_mode_id="failure:remote-calendar-address",
                name="address-is-not-attendance-proof",
                description="An address in a calendar event does not prove physical attendance.",
                trigger="event has address and is likely remote",
                evidence=("fixture:calendar-address", "fixture:calendar-remote-likely"),
                severity="medium",
                mitigations=(
                    "retain uncertainty",
                    "do not infer travel from address alone",
                ),
                source_episode_ids=("episode:calendar-remote-event",),
            ),
            FailureMode(
                failure_mode_id="failure:traffic-outage",
                name="traffic-service-outage",
                description="Traffic is unavailable and cannot justify a precise travel estimate.",
                trigger="traffic_available is false",
                evidence=("fixture:traffic-outage",),
                severity="medium",
                mitigations=(
                    "use a labeled fallback buffer",
                    "avoid false precision",
                    "recalculate later",
                ),
                source_episode_ids=("episode:traffic-outage",),
            ),
        )
        for mode in modes:
            self.store.save_failure_mode(mode)

    def _departure_candidates(self, fixture: DepartureFixture) -> list[CandidatePlan]:
        known = tuple(item.failure_mode_id for item in self.store.list_failure_modes())
        common = {
            "source_episode_ids": (
                "episode:departure-cover-failure",
                "episode:calendar-remote-event",
            ),
            "known_failure_mode_ids": known,
            "context_fingerprint": fixture.fingerprint(),
        }

        def observation(
            candidate_id: str,
            key: str,
            value: Any,
            description: str,
            observer: str,
            confidence: float,
            assumptions: tuple[str, ...] = (),
        ) -> ExpectedObservation:
            return ExpectedObservation(
                observation_id=stable_id("expected", candidate_id, key),
                candidate_id=candidate_id,
                key=key,
                description=description,
                expected_value=value,
                observable_by=observer,
                confidence=confidence,
                provenance={"fixture": fixture.scenario_id, "layer": "phase9"},
                assumptions=assumptions,
            )

        def action(
            candidate_id: str,
            name: str,
            action_type: str,
            target: str,
            params: dict[str, Any],
            obs: ExpectedObservation,
            capability: str,
            consequence: str,
            reversible: bool,
            rationale: str,
        ) -> TypedAction:
            return TypedAction(
                action_id=stable_id("action", candidate_id, name),
                candidate_id=candidate_id,
                action_type=action_type,
                target=target,
                parameters=params,
                capability=capability,
                consequence_class=consequence,
                reversible=reversible,
                expected_observation_ids=(obs.observation_id,),
                rationale=rationale,
            )

        one = "candidate:full-away-automation"
        one_cover = observation(
            one,
            "cover_closed",
            True,
            "The entry cover will be closed and independently observed.",
            "home-assistant-twin",
            0.92,
        )
        one_presence = observation(
            one,
            "physical_attendance",
            True,
            "The calendar event represents physical attendance.",
            "personal-event-replay",
            0.45,
            ("calendar address is weak evidence",),
        )
        one_traffic = observation(
            one,
            "traffic_available",
            True,
            "A precise traffic estimate will be available.",
            "traffic-service",
            0.35,
        )
        candidate_one = CandidatePlan(
            candidate_id=one,
            scenario_id=fixture.scenario_id,
            strategy_family="full-away-automation",
            title="Run the full away routine",
            rationale="Optimize for maximum unattended preparation.",
            actions=(
                action(
                    one,
                    "close-cover",
                    "home_assistant.cover.position",
                    fixture.cover_entity,
                    {"position": 0},
                    one_cover,
                    "home-control",
                    "device",
                    True,
                    "Close the entry cover before departure.",
                ),
                action(
                    one,
                    "arm-alarm",
                    "home_assistant.alarm.arm",
                    "alarm.home",
                    {},
                    one_presence,
                    "security-control",
                    "security",
                    True,
                    "Arm the alarm as part of the away routine.",
                ),
                action(
                    one,
                    "turn-entry-light-off",
                    "home_assistant.light.off",
                    "light.entry",
                    {},
                    one_traffic,
                    "home-control",
                    "device",
                    True,
                    "Turn off the entry light.",
                ),
            ),
            expected_observations=(one_cover, one_presence, one_traffic),
            assumptions=(
                "Marc is the only person at home",
                "calendar address proves physical attendance",
                "traffic is available",
            ),
            diversity_signature=(
                "automate",
                "alarm-arm",
                "cover-close",
                "traffic-strict",
                "presence-ignore",
            ),
            **common,
        )

        two = "candidate:presence-aware-preparation"
        two_cover = observation(
            two,
            "cover_closed",
            True,
            "The entry cover will be closed after a feedback check.",
            "home-assistant-twin",
            0.90,
        )
        two_presence = observation(
            two,
            "household_safe",
            True,
            "Preparation will not inconvenience the other household member.",
            "presence-replay",
            0.82,
        )
        two_traffic = observation(
            two,
            "departure_buffer_minutes",
            fixture.traffic_fallback_minutes,
            "A labeled fallback departure buffer will be used while traffic is offline.",
            "symbolic-simulator",
            0.85,
        )
        candidate_two = CandidatePlan(
            candidate_id=two,
            scenario_id=fixture.scenario_id,
            strategy_family="presence-aware-preparation",
            title="Prepare common areas while preserving household safety",
            rationale="Avoid alarm arming, but still automate device preparation.",
            actions=(
                action(
                    two,
                    "close-cover",
                    "home_assistant.cover.position",
                    fixture.cover_entity,
                    {"position": 0},
                    two_cover,
                    "home-control",
                    "device",
                    True,
                    "Close the cover only after checking its feedback.",
                ),
                action(
                    two,
                    "turn-entry-light-off",
                    "home_assistant.light.off",
                    "light.entry",
                    {},
                    two_presence,
                    "home-control",
                    "device",
                    True,
                    "Turn off a common-area light after departure.",
                ),
            ),
            expected_observations=(two_cover, two_presence, two_traffic),
            assumptions=(
                "the feedback check is trustworthy",
                "the other household member is not using the entry light",
            ),
            diversity_signature=(
                "prepare",
                "no-alarm",
                "cover-close",
                "traffic-fallback",
                "presence-aware",
            ),
            **common,
        )

        three = "candidate:degraded-service-consent"
        three_cover = observation(
            three,
            "cover_state",
            fixture.cover_observed_state,
            "The cover state will be read and left explicit when feedback is contradictory.",
            "home-assistant-twin",
            0.97,
        )
        three_attendance = observation(
            three,
            "physical_attendance",
            "unknown",
            "Physical attendance remains unknown because a calendar address is not proof for a likely remote event.",
            "personal-event-replay",
            0.96,
            ("event is likely remote",),
        )
        three_traffic = observation(
            three,
            "departure_buffer_minutes",
            fixture.traffic_fallback_minutes,
            "A labeled fallback buffer will be shown instead of false traffic precision.",
            "service-dry-run",
            0.94,
            ("traffic service is offline",),
        )
        candidate_three = CandidatePlan(
            candidate_id=three,
            scenario_id=fixture.scenario_id,
            strategy_family="degraded-service-consent",
            title="Degrade safely and ask for confirmation",
            rationale="Keep the household untouched, surface uncertainty, and preserve a later Guardian decision point.",
            actions=(
                action(
                    three,
                    "read-cover",
                    "home_assistant.read_state",
                    fixture.cover_entity,
                    {},
                    three_cover,
                    "home-read",
                    "observation",
                    True,
                    "Read the cover and do not treat reported success as movement.",
                ),
                action(
                    three,
                    "request-confirmation",
                    "notification.reminder",
                    "Marc",
                    {
                        "message": "Traffic is unavailable; another household member is present; confirm any departure changes."
                    },
                    three_attendance,
                    "notification",
                    "communication",
                    True,
                    "Ask Marc before any consequential change.",
                ),
            ),
            expected_observations=(three_cover, three_attendance, three_traffic),
            assumptions=(
                "traffic may return later",
                "Marc can confirm consequential changes",
                "the digital twin is not authority",
            ),
            diversity_signature=(
                "observe",
                "consent",
                "no-device-effects",
                "traffic-fallback",
                "presence-preserving",
            ),
            **common,
        )
        return [candidate_one, candidate_two, candidate_three]

    def _safe_modification(
        self, candidate: CandidatePlan, fixture: DepartureFixture
    ) -> CandidatePlan | None:
        if candidate.strategy_family != "presence-aware-preparation":
            return None
        # Replace the contradictory device command with an observation-only
        # action, preserving the candidate identity and causal link.
        cover_obs = next(
            item
            for item in candidate.expected_observations
            if item.key == "cover_closed"
        )
        revised_obs = replace(
            cover_obs,
            key="cover_state",
            description="The entry cover state will be read and left explicit after feedback contradiction.",
            expected_value=fixture.cover_observed_state,
            confidence=0.97,
        )
        revised_action = replace(
            next(
                item
                for item in candidate.actions
                if item.action_type == "home_assistant.cover.position"
            ),
            action_type="home_assistant.read_state",
            parameters={},
            capability="home-read",
            consequence_class="observation",
            rationale="Do not command movement after contradictory feedback; read state only.",
            expected_observation_ids=(revised_obs.observation_id,),
        )
        revised_observations = tuple(
            revised_obs if item.observation_id == cover_obs.observation_id else item
            for item in candidate.expected_observations
        )
        return replace(
            candidate,
            actions=(revised_action,),
            expected_observations=revised_observations,
            status=CandidateStatus.MODIFIED,
            modification_notes=(
                "cover command removed after feedback contradiction",
                "alarm was never added because household presence remained explicit",
            ),
            rationale="Modify to observation-only preparation after seeded contradiction.",
        )

    def _critics(
        self, candidate: CandidatePlan, fixture: DepartureFixture
    ) -> list[CriticResult]:
        failure_findings: list[str] = []
        failure_evidence: list[str] = []
        safety_findings: list[str] = []
        safety_evidence: list[str] = []
        feasibility_findings: list[str] = []
        feasibility_evidence: list[str] = []
        if (
            any(
                action.action_type == "home_assistant.cover.position"
                for action in candidate.actions
            )
            and fixture.cover_reported_state != fixture.cover_observed_state
        ):
            failure_findings.append(
                "cover-feedback contradiction: command reports success but cover did not move"
            )
            failure_evidence.extend(
                ["fixture:cover-reported-success", "fixture:cover-state-unchanged"]
            )
        if fixture.household_present and any(
            person != "Marc" for person in fixture.household_present
        ):
            if any(
                action.action_type == "home_assistant.alarm.arm"
                for action in candidate.actions
            ):
                safety_findings.append(
                    "another household member is present; alarm arming is inappropriate"
                )
                safety_evidence.append("fixture:presence-guest")
            if any(
                action.action_type
                in {"home_assistant.light.off", "home_assistant.turn_off"}
                for action in candidate.actions
            ):
                safety_findings.append(
                    "another household member is present; turning off a common-area light needs consent"
                )
                safety_evidence.append("fixture:presence-guest")
        if (
            fixture.calendar_address
            and fixture.calendar_likely_remote
            and any(
                item.key == "physical_attendance" and item.expected_value is True
                for item in candidate.expected_observations
            )
        ):
            failure_findings.append(
                "calendar address is not proof of physical attendance"
            )
            failure_evidence.extend(
                ["fixture:calendar-address", "fixture:calendar-remote-likely"]
            )
        if (
            not fixture.traffic_available
            and "traffic-strict" in candidate.diversity_signature
        ):
            feasibility_findings.append(
                "traffic outage invalidates the candidate's strict traffic assumption"
            )
            feasibility_evidence.append("fixture:traffic-outage")
        return [
            CriticResult(
                critic_id=stable_id(
                    "critic", candidate.candidate_id, CriticKind.FAILURE_MODE.value
                ),
                candidate_id=candidate.candidate_id,
                kind=CriticKind.FAILURE_MODE,
                passed=not failure_findings,
                severity="high" if failure_findings else "info",
                findings=tuple(failure_findings)
                or ("no seeded failure contradiction remained",),
                evidence_ids=tuple(failure_evidence),
            ),
            CriticResult(
                critic_id=stable_id(
                    "critic", candidate.candidate_id, CriticKind.SAFETY.value
                ),
                candidate_id=candidate.candidate_id,
                kind=CriticKind.SAFETY,
                passed=not safety_findings,
                severity="high" if safety_findings else "info",
                findings=tuple(safety_findings)
                or ("household-presence constraint satisfied",),
                evidence_ids=tuple(safety_evidence),
            ),
            CriticResult(
                critic_id=stable_id(
                    "critic", candidate.candidate_id, CriticKind.FEASIBILITY.value
                ),
                candidate_id=candidate.candidate_id,
                kind=CriticKind.FEASIBILITY,
                passed=not feasibility_findings,
                severity="medium" if feasibility_findings else "info",
                findings=tuple(feasibility_findings)
                or ("schedule, dependency, and service assumptions are bounded",),
                evidence_ids=tuple(feasibility_evidence),
                suggested_modifications=("use a labeled fallback buffer",)
                if feasibility_findings
                else (),
            ),
        ]

    def _score(self, candidate: CandidatePlan, critics: list[CriticResult]) -> Score:
        failed = {item.kind for item in critics if not item.passed}
        dimensions = {
            ScoreDimension.SUCCESS.value: 0.85
            if candidate.status is not CandidateStatus.REJECTED
            else 0.15,
            ScoreDimension.SAFETY.value: 0.95
            if CriticKind.SAFETY not in failed
            else 0.10,
            ScoreDimension.PRIVACY.value: 0.90,
            ScoreDimension.REVERSIBILITY.value: 0.90
            if all(item.reversible for item in candidate.actions)
            else 0.65,
            ScoreDimension.COST.value: 0.85 if len(candidate.actions) <= 2 else 0.45,
            ScoreDimension.LATENCY.value: 0.75
            if "traffic-fallback" in candidate.diversity_signature
            else 0.35,
            ScoreDimension.MARC_BURDEN.value: 0.75
            if "consent" in candidate.diversity_signature
            else 0.45,
        }
        weights = {
            "success": 0.25,
            "safety": 0.25,
            "privacy": 0.10,
            "reversibility": 0.10,
            "cost": 0.10,
            "latency": 0.10,
            "marc_burden": 0.10,
        }
        total = sum(dimensions[key] * weights[key] for key in dimensions)
        return Score(
            score_id=stable_id("score", candidate.candidate_id),
            candidate_id=candidate.candidate_id,
            dimensions=dimensions,
            weighted_total=round(total, 4),
            rationale="Weighted success, safety, privacy, reversibility, cost, latency, and Marc-burden score after deterministic critics.",
            critic_ids=tuple(item.critic_id for item in critics),
        )

    def _decision_reason(
        self, selected: CandidatePlan | None, candidates: list[CandidatePlan]
    ) -> str:
        rejected = [
            item.title for item in candidates if item.status is CandidateStatus.REJECTED
        ]
        selected_text = selected.title if selected else "no candidate"
        return (
            f"Selected {selected_text}. Rejected {', '.join(rejected) or 'none'} after comparing materially different strategies. "
            "The cover feedback contradiction, household presence, likely-remote calendar event, and traffic outage were treated as explicit evidence; "
            "the address did not prove physical attendance, and the traffic outage used a labeled fallback rather than false precision. "
            "Only candidates that passed pre-Guardian critics are eligible for Guardian revalidation."
        )


def _values_equal(observed: Any, expected: Any) -> bool:
    """Compare replay values without treating booleans as integers."""

    if isinstance(observed, bool) or isinstance(expected, bool):
        return type(observed) is type(expected) and observed == expected
    return observed == expected


__all__ = ["GuardianBoundary", "GuardianRevalidation", "ImaginationService"]
