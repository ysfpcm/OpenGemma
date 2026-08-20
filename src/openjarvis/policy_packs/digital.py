# ruff: noqa: E501
"""Bounded Codex/PC mission-template pack foundation for Phase 12."""

from __future__ import annotations

from .contracts import (
    ActionSpec,
    DigitalMissionTemplate,
    InstallationGrant,
    PackInstallation,
    PolicyEvaluation,
    PolicyEvent,
    PolicyOutcome,
    PolicyPackManifest,
    ReleaseGate,
    ReplayFixtureRef,
    new_id,
)

DIGITAL_PUBLISHER = "openjarvis.first-party.digital"
DIGITAL_SIGNING_KEY = "phase12-digital-fixture-key"


class DigitalEmbodimentPack:
    def __init__(
        self, *, signing_key: str = DIGITAL_SIGNING_KEY, version: str = "1.0.0"
    ) -> None:
        self.manifest = digital_embodiment_manifest(
            signing_key=signing_key, version=version
        )
        self.templates = (
            DigitalMissionTemplate(
                "codex.local.observe",
                "Observe local workspace",
                "inspect {workspace}",
                ("digital.mission.propose",),
                network_allowed=False,
                explanation="Read-only local observation.",
            ),
            DigitalMissionTemplate(
                "codex.local.navigate",
                "Navigate local workspace",
                "navigate {workspace}",
                ("digital.mission.propose",),
                network_allowed=False,
                explanation="Bounded local navigation proposal.",
            ),
            DigitalMissionTemplate(
                "codex.local.fixture-write",
                "Write an isolated fixture",
                "modify {fixture_root}",
                ("digital.mission.propose",),
                writable_roots=("fixture-root",),
                network_allowed=False,
                explanation="Fixture-only write proposal; production activation is disabled.",
            ),
        )

    def evaluate(
        self, event: PolicyEvent, installation: PackInstallation
    ) -> PolicyEvaluation:
        return PolicyEvaluation(
            evaluation_id=new_id("digital-evaluation"),
            event_id=event.event_id,
            pack_id=installation.manifest.pack_id,
            pack_version=installation.manifest.version,
            outcome=PolicyOutcome.NO_ACTION,
            explanation="Digital embodiment templates describe bounded local work; they do not silently execute or widen authority.",
            evidence_ids=tuple(item.evidence_id for item in event.evidence),
            timeline=(),
            simulation_only=True,
            live_effects=False,
        )


def digital_embodiment_manifest(
    *, signing_key: str = DIGITAL_SIGNING_KEY, version: str = "1.0.0"
) -> PolicyPackManifest:
    base = PolicyPackManifest(
        pack_id="digital-embodiment",
        name="Digital Embodiment / Codex Missions",
        version=version,
        publisher=DIGITAL_PUBLISHER,
        signature="pending",
        integrity_digest="pending",
        triggers=("codex-mission", "local-workspace"),
        required_sources=("workspace-authority",),
        situation_detector="digital.embodiment.v1",
        evidence_requirements=("workspace-root", "sandbox", "network-policy"),
        supported_actions=(
            ActionSpec(
                "digital.mission.propose",
                "codex:local-mission",
                "low",
                "low",
                {
                    "type": "object",
                    "required": ["template_id", "workspace"],
                    "properties": {
                        "template_id": {"type": "string"},
                        "workspace": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "digital.mission.propose",
            ),
            ActionSpec(
                "embodiment.simulated_actuator",
                "embodiment:simulate",
                "low",
                "low",
                {
                    "type": "object",
                    "required": [
                        "command_id",
                        "actuator",
                        "operation",
                        "target",
                        "value",
                    ],
                    "properties": {
                        "command_id": {"type": "string"},
                        "actuator": {"type": "string"},
                        "operation": {"type": "string"},
                        "target": {"type": "string"},
                        "value": {"type": "number"},
                    },
                    "additionalProperties": False,
                },
                "embodiment.simulated_actuator",
            ),
        ),
        autonomy_defaults={
            "intent": "proposal-only",
            "controller": "deterministic",
            "execution": "simulation-only",
            "production": "disabled",
        },
        authority_limits={
            "allowed_action_types": [
                "digital.mission.propose",
                "embodiment.simulated_actuator",
            ],
            "allowed_writable_roots": ["fixture-root"],
            "network": "disabled",
            "max_risk_class": "low",
            "max_consequence_class": "low",
        },
        ui_metadata={
            "display_name": "Digital Embodiment",
            "explanation_style": "causal-timeline",
            "sensitivity": "metadata-only",
        },
        replay_fixtures=(
            ReplayFixtureRef(
                "digital-proposal", "bounded Codex mission proposal", "no_action"
            ),
        ),
        fault_cases=(
            "scope-drift",
            "network-request",
            "writable-root-escape",
            "revoked-installation",
        ),
        release_gates=(
            ReleaseGate(
                "no-live-activation",
                "digital templates cannot activate production",
                True,
                True,
            ),
        ),
        lifecycle_behavior={
            "install": "inactive until explicit use",
            "uninstall": "retain template provenance",
            "rollback": "restore prior template version",
        },
    )
    return base.signed(signing_key)


def digital_installation_grant(
    installation_id: str, *, grant_id: str | None = None
) -> InstallationGrant:
    return InstallationGrant(
        grant_id=grant_id or new_id("digital-grant"),
        pack_id="digital-embodiment",
        installation_id=installation_id,
        allowed_action_types=(
            "digital.mission.propose",
            "embodiment.simulated_actuator",
        ),
        allowed_capabilities=("codex:local-mission", "embodiment:simulate"),
        target_scope={"target": "virtual-actuator"},
        max_risk_class="low",
        max_consequence_class="low",
        installed_by="Marc",
        provenance={"source": "phase12-acceptance-fixture", "simulation_only": True},
    )
