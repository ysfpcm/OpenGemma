# ruff: noqa: E501
"""Arrival Guardian as an ordinary, replayable Phase 12 policy pack."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .contracts import (
    ActionSpec,
    CausalTimelineEntry,
    InstallationGrant,
    PackInstallation,
    PolicyActionProposal,
    PolicyEvaluation,
    PolicyEvent,
    PolicyOutcome,
    PolicyPackManifest,
    ReleaseGate,
    ReplayFixtureRef,
    new_id,
)

ARRIVAL_PUBLISHER = "openjarvis.first-party"
ARRIVAL_SIGNING_KEY = "phase12-arrival-fixture-key"


class ArrivalGuardianPack:
    """Policy logic kept outside Guardian Kernel and the verified executor."""

    def __init__(
        self, *, signing_key: str = ARRIVAL_SIGNING_KEY, version: str = "1.0.0"
    ) -> None:
        self.manifest = arrival_manifest(signing_key=signing_key, version=version)

    def evaluate(
        self, event: PolicyEvent, installation: PackInstallation
    ) -> PolicyEvaluation:
        evidence_ids = tuple(item.evidence_id for item in event.evidence)
        reasons: list[str] = []
        missing = set(self.manifest.required_sources) - set(event.sources)
        if missing:
            reasons.append("missing-sources:" + ",".join(sorted(missing)))
        stale = [
            item.evidence_id
            for item in event.evidence
            if not item.is_fresh(event.observed_at)
        ]
        if stale:
            reasons.append("stale-evidence:" + ",".join(stale))
        missing_evidence = set(self.manifest.required_sources) - {
            item.source_id for item in event.evidence
        }
        if missing_evidence:
            reasons.append("missing-evidence:" + ",".join(sorted(missing_evidence)))
        provenance_missing = [
            item.evidence_id for item in event.evidence if not item.provenance
        ]
        if provenance_missing:
            reasons.append("missing-provenance:" + ",".join(provenance_missing))
        try:
            event_time = datetime.fromisoformat(
                event.observed_at.replace("Z", "+00:00")
            )
            if event_time > datetime.now(timezone.utc):
                reasons.append("future-event")
        except (TypeError, ValueError):
            reasons.append("invalid-event-time")
        contradictory = [
            item.evidence_id for item in event.evidence if item.contradictory
        ]
        if contradictory:
            reasons.append("contradictory-evidence:" + ",".join(contradictory))
        low_confidence = [
            item.evidence_id for item in event.evidence if item.confidence < 0.7
        ]
        if low_confidence:
            reasons.append("insufficient-confidence:" + ",".join(low_confidence))

        scenario = event.scenario.lower().replace("_", "-")
        if scenario == "false-presence":
            reasons.append("false-presence-suppressed")
        if scenario == "service-outage":
            reasons.append("service-outage-fail-closed")
        if reasons:
            return self._evaluation(
                event,
                installation,
                PolicyOutcome.BLOCKED,
                "Arrival Guardian made no proposal because required evidence or safety conditions failed.",
                tuple(reasons),
                evidence_ids,
                (),
            )

        proposals: list[PolicyActionProposal] = []
        if scenario == "arrival":
            proposals.extend(
                [
                    PolicyActionProposal(
                        proposal_id=new_id("arrival-plan"),
                        action_type="arrival.prepare_entry",
                        parameters={"target": "simulated-home", "mode": "arrival"},
                        risk_class="low",
                        consequence_class="low",
                        plan_id=new_id("arrival-plan-record"),
                        installation_id=installation.installation_id,
                        idempotency_key=f"arrival:{event.dedupe_key}:prepare-entry",
                        expected_effect={"prepared": True},
                    ),
                    PolicyActionProposal(
                        proposal_id=new_id("arrival-plan"),
                        action_type="arrival.notify_household",
                        parameters={
                            "target": "simulated-household",
                            "scenario": "arrival",
                        },
                        risk_class="low",
                        consequence_class="low",
                        plan_id=new_id("arrival-plan-record"),
                        installation_id=installation.installation_id,
                        idempotency_key=f"arrival:{event.dedupe_key}:notify",
                        expected_effect={"notified": True},
                    ),
                ]
            )
            explanation = "Arrival evidence is fresh and non-contradictory; bounded preparation and notification are proposed for review."
        elif scenario == "guest-present":
            proposals.append(
                PolicyActionProposal(
                    proposal_id=new_id("arrival-plan"),
                    action_type="arrival.notify_household",
                    parameters={
                        "target": "simulated-household",
                        "scenario": "guest-present",
                    },
                    risk_class="low",
                    consequence_class="low",
                    plan_id=new_id("arrival-plan-record"),
                    installation_id=installation.installation_id,
                    idempotency_key=f"arrival:{event.dedupe_key}:guest",
                    expected_effect={"notified": True},
                )
            )
            explanation = "Guest-presence evidence is recorded; Arrival Guardian proposes a bounded explanation-only notification."
        elif scenario == "vacation":
            proposals.append(
                PolicyActionProposal(
                    proposal_id=new_id("arrival-plan"),
                    action_type="arrival.set_away_scene",
                    parameters={"target": "simulated-home", "mode": "vacation"},
                    risk_class="low",
                    consequence_class="low",
                    plan_id=new_id("arrival-plan-record"),
                    installation_id=installation.installation_id,
                    idempotency_key=f"arrival:{event.dedupe_key}:vacation",
                    expected_effect={"away_scene": True},
                )
            )
            explanation = "Vacation evidence is fresh; a reversible away-scene proposal is available without live activation."
        else:
            reasons.append("unrecognized-scenario")
            return self._evaluation(
                event,
                installation,
                PolicyOutcome.NO_ACTION,
                "No Arrival Guardian action is declared for this scenario.",
                tuple(reasons),
                evidence_ids,
                (),
            )
        return self._evaluation(
            event,
            installation,
            PolicyOutcome.PROPOSED,
            explanation,
            tuple(reasons),
            evidence_ids,
            tuple(proposals),
        )

    @staticmethod
    def _evaluation(
        event: PolicyEvent,
        installation: PackInstallation,
        outcome: PolicyOutcome,
        explanation: str,
        reasons: tuple[str, ...],
        evidence_ids: tuple[str, ...],
        proposals: tuple[PolicyActionProposal, ...],
    ) -> PolicyEvaluation:
        timeline: list[CausalTimelineEntry] = [
            CausalTimelineEntry(
                "Plan",
                proposals[0].plan_id if proposals else new_id("arrival-no-plan"),
                "proposed" if proposals else "not-created",
                explanation,
                (event.event_id,),
            ),
        ]
        for proposal in proposals:
            timeline.extend(
                [
                    CausalTimelineEntry(
                        "Authorization",
                        proposal.proposal_id,
                        "not-requested",
                        "Installation-bounded proposal; Guardian authorization is a separate step.",
                        (proposal.plan_id,),
                    ),
                    CausalTimelineEntry(
                        "ActionAttempt",
                        proposal.proposal_id,
                        "not-attempted",
                        "Replay is simulation-only and produced no effect.",
                        (proposal.proposal_id,),
                    ),
                    CausalTimelineEntry(
                        "Verification",
                        proposal.proposal_id,
                        "not-applicable",
                        "No real-world effect was authorized or executed.",
                        (proposal.proposal_id,),
                    ),
                ]
            )
        return PolicyEvaluation(
            evaluation_id=new_id("arrival-evaluation"),
            event_id=event.event_id,
            pack_id=installation.manifest.pack_id,
            pack_version=installation.manifest.version,
            outcome=outcome,
            explanation=explanation,
            reasons=reasons,
            evidence_ids=evidence_ids,
            proposals=proposals,
            timeline=tuple(timeline),
            reversible=True,
            simulation_only=True,
            live_effects=False,
        )


def arrival_manifest(
    *, signing_key: str = ARRIVAL_SIGNING_KEY, version: str = "1.0.0"
) -> PolicyPackManifest:
    base = PolicyPackManifest(
        pack_id="arrival-guardian",
        name="Arrival Guardian",
        version=version,
        publisher=ARRIVAL_PUBLISHER,
        signature="pending",
        integrity_digest="pending",
        triggers=("arrival", "guest-present", "vacation"),
        required_sources=("presence", "calendar", "connectivity"),
        situation_detector="arrival.guardian.v1",
        evidence_requirements=(
            "fresh",
            "non-contradictory",
            "provenance",
            "confidence>=0.7",
        ),
        supported_actions=(
            ActionSpec(
                "arrival.prepare_entry",
                "arrival:prepare",
                "low",
                "low",
                {
                    "type": "object",
                    "required": ["target", "mode"],
                    "properties": {
                        "target": {"type": "string"},
                        "mode": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "arrival.prepare_entry",
            ),
            ActionSpec(
                "arrival.notify_household",
                "arrival:notify",
                "low",
                "low",
                {
                    "type": "object",
                    "required": ["target", "scenario"],
                    "properties": {
                        "target": {"type": "string"},
                        "scenario": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "arrival.notify_household",
            ),
            ActionSpec(
                "arrival.set_away_scene",
                "arrival:scene",
                "low",
                "low",
                {
                    "type": "object",
                    "required": ["target", "mode"],
                    "properties": {
                        "target": {"type": "string"},
                        "mode": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "arrival.set_away_scene",
            ),
        ),
        autonomy_defaults={
            "proposal": "manual-review",
            "execution": "disabled",
            "production": "disabled",
        },
        authority_limits={
            "allowed_action_types": [
                "arrival.prepare_entry",
                "arrival.notify_household",
                "arrival.set_away_scene",
            ],
            "max_risk_class": "low",
            "max_consequence_class": "low",
        },
        ui_metadata={
            "display_name": "Arrival Guardian",
            "explanation_style": "causal-timeline",
            "sensitivity": "operational-only",
        },
        replay_fixtures=tuple(
            ReplayFixtureRef(
                item,
                item,
                "blocked"
                if item in {"false-presence", "service-outage"}
                else "proposed",
            )
            for item in (
                "arrival",
                "guest-present",
                "vacation",
                "false-presence",
                "service-outage",
            )
        ),
        fault_cases=(
            "invalid-signature",
            "missing-dependency",
            "stale-evidence",
            "contradictory-presence",
            "migration-failure",
            "duplicate-event",
            "revoked-installation",
        ),
        release_gates=(
            ReleaseGate(
                "arrival-replay",
                "all five deterministic Arrival scenarios replay locally",
                True,
                True,
            ),
            ReleaseGate(
                "no-core-branch",
                "pack is installed without Guardian-specific branches",
                True,
                True,
            ),
        ),
        lifecycle_behavior={
            "install": "retain provenance and remain inactive",
            "upgrade": "apply reversible migration",
            "downgrade": "restore previous manifest",
            "uninstall": "retain audit history and remove live availability",
            "rollback": "restore last verified version",
        },
    )
    return base.signed(signing_key)


def arrival_installation_grant(
    installation_id: str, *, grant_id: Optional[str] = None
) -> InstallationGrant:
    return InstallationGrant(
        grant_id=grant_id or new_id("arrival-grant"),
        pack_id="arrival-guardian",
        installation_id=installation_id,
        allowed_action_types=(
            "arrival.prepare_entry",
            "arrival.notify_household",
            "arrival.set_away_scene",
        ),
        allowed_capabilities=("arrival:prepare", "arrival:notify", "arrival:scene"),
        target_scope={},
        max_risk_class="low",
        max_consequence_class="low",
        installed_by="Marc",
        provenance={"source": "phase12-acceptance-fixture", "simulation_only": True},
    )
