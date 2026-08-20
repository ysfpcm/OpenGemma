"""Typed, model-independent Phase 9 imagination contracts.

The imagination layer is deliberately a prediction and proposal system.  Its
records carry an explicit simulation scope and an explicit Guardian boundary;
none of these contracts imply that a real-world effect happened.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

PHASE9_SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        canonical_json([prefix, *parts]).encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}_{digest}"


class CandidateStatus(str, Enum):
    PROPOSED = "proposed"
    SIMULATED = "simulated"
    MODIFIED = "modified"
    VALID = "valid"
    REJECTED = "rejected"
    GUARDIAN_PENDING = "guardian_pending"
    GUARDIAN_REVALIDATED = "guardian_revalidated"
    CANCELED = "canceled"


class PredictionStatus(str, Enum):
    SIMULATED = "simulated"
    PROPOSED = "proposed"
    ATTEMPTED = "attempted"
    OBSERVED = "observed"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class SimulationKind(str, Enum):
    SYMBOLIC = "symbolic_schedule_dependency_budget"
    PERSONAL_EVENT_REPLAY = "personal_event_replay"
    SERVICE_DRY_RUN = "service_api_dry_run"
    CODEX_WORKSPACE = "codex_isolated_workspace"
    HOME_ASSISTANT_TWIN = "home_assistant_digital_twin"


class SimulationStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class CriticKind(str, Enum):
    FEASIBILITY = "feasibility"
    FAILURE_MODE = "failure_mode"
    SAFETY = "safety"
    PRIVACY = "privacy"


class ScoreDimension(str, Enum):
    SUCCESS = "success"
    SAFETY = "safety"
    PRIVACY = "privacy"
    REVERSIBILITY = "reversibility"
    COST = "cost"
    LATENCY = "latency"
    MARC_BURDEN = "marc_burden"


@dataclass(frozen=True, slots=True)
class ExpectedObservation:
    """An observable claim that can later be compared with replayed evidence."""

    observation_id: str
    candidate_id: str
    key: str
    description: str
    expected_value: Any
    observable_by: str
    confidence: float
    provenance: Mapping[str, Any] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    causal_parents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.observation_id or not self.candidate_id or not self.key:
            raise ValueError("expected observation identity is required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("expected observation confidence must be between 0 and 1")
        if not self.observable_by:
            raise ValueError("expected observation must name an observer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "candidate_id": self.candidate_id,
            "key": self.key,
            "description": self.description,
            "expected_value": self.expected_value,
            "observable_by": self.observable_by,
            "confidence": self.confidence,
            "provenance": dict(self.provenance),
            "assumptions": list(self.assumptions),
            "causal_parents": list(self.causal_parents),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExpectedObservation":
        _check_version(value)
        return cls(
            observation_id=str(value["observation_id"]),
            candidate_id=str(value["candidate_id"]),
            key=str(value["key"]),
            description=str(value["description"]),
            expected_value=value.get("expected_value"),
            observable_by=str(value["observable_by"]),
            confidence=float(value["confidence"]),
            provenance=dict(value.get("provenance", {})),
            assumptions=tuple(str(item) for item in value.get("assumptions", [])),
            causal_parents=tuple(str(item) for item in value.get("causal_parents", [])),
        )


@dataclass(frozen=True, slots=True)
class TypedAction:
    """A typed proposal that still requires Guardian authority to execute."""

    action_id: str
    candidate_id: str
    action_type: str
    target: str
    parameters: Mapping[str, Any]
    capability: str
    consequence_class: str
    reversible: bool
    expected_observation_ids: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    rationale: str = ""
    guardian_required: bool = True

    def __post_init__(self) -> None:
        if (
            not self.action_id
            or not self.candidate_id
            or not self.action_type
            or not self.target
        ):
            raise ValueError("typed action identity and type are required")
        if not self.guardian_required:
            raise ValueError("Phase 9 actions must remain Guardian-bound")
        if not self.expected_observation_ids:
            raise ValueError("typed action must link an expected observation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "action_id": self.action_id,
            "candidate_id": self.candidate_id,
            "action_type": self.action_type,
            "target": self.target,
            "parameters": dict(self.parameters),
            "capability": self.capability,
            "consequence_class": self.consequence_class,
            "reversible": self.reversible,
            "expected_observation_ids": list(self.expected_observation_ids),
            "dependencies": list(self.dependencies),
            "rationale": self.rationale,
            "guardian_required": self.guardian_required,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TypedAction":
        _check_version(value)
        return cls(
            action_id=str(value["action_id"]),
            candidate_id=str(value["candidate_id"]),
            action_type=str(value["action_type"]),
            target=str(value["target"]),
            parameters=dict(value.get("parameters", {})),
            capability=str(value["capability"]),
            consequence_class=str(value["consequence_class"]),
            reversible=bool(value["reversible"]),
            expected_observation_ids=tuple(
                str(item) for item in value.get("expected_observation_ids", [])
            ),
            dependencies=tuple(str(item) for item in value.get("dependencies", [])),
            rationale=str(value.get("rationale", "")),
            guardian_required=bool(value.get("guardian_required", True)),
        )


@dataclass(frozen=True, slots=True)
class CandidatePlan:
    candidate_id: str
    scenario_id: str
    strategy_family: str
    title: str
    rationale: str
    actions: tuple[TypedAction, ...]
    expected_observations: tuple[ExpectedObservation, ...]
    assumptions: tuple[str, ...]
    diversity_signature: tuple[str, ...]
    source_episode_ids: tuple[str, ...] = ()
    known_failure_mode_ids: tuple[str, ...] = ()
    status: CandidateStatus = CandidateStatus.PROPOSED
    rejection_reasons: tuple[str, ...] = ()
    modification_notes: tuple[str, ...] = ()
    context_fingerprint: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.scenario_id or not self.strategy_family:
            raise ValueError("candidate identity and strategy are required")
        if not self.actions or not self.expected_observations:
            raise ValueError("candidate must contain actions and expected observations")
        if len(set(self.diversity_signature)) < 2:
            raise ValueError("candidate diversity signature is too small")
        action_ids = {item.action_id for item in self.actions}
        if len(action_ids) != len(self.actions):
            raise ValueError("candidate action IDs must be unique")
        if any(item.candidate_id != self.candidate_id for item in self.actions):
            raise ValueError("typed action belongs to another candidate")
        if any(
            item.candidate_id != self.candidate_id
            for item in self.expected_observations
        ):
            raise ValueError("expected observation belongs to another candidate")
        observation_ids = {item.observation_id for item in self.expected_observations}
        if len(observation_ids) != len(self.expected_observations):
            raise ValueError("candidate observation IDs must be unique")
        for action in self.actions:
            if not set(action.expected_observation_ids) <= observation_ids:
                raise ValueError("action references an unknown expected observation")
            if not set(action.dependencies) <= action_ids:
                raise ValueError("action references an unknown dependency")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "candidate_id": self.candidate_id,
            "scenario_id": self.scenario_id,
            "strategy_family": self.strategy_family,
            "title": self.title,
            "rationale": self.rationale,
            "actions": [item.to_dict() for item in self.actions],
            "expected_observations": [
                item.to_dict() for item in self.expected_observations
            ],
            "assumptions": list(self.assumptions),
            "diversity_signature": list(self.diversity_signature),
            "source_episode_ids": list(self.source_episode_ids),
            "known_failure_mode_ids": list(self.known_failure_mode_ids),
            "status": self.status.value,
            "rejection_reasons": list(self.rejection_reasons),
            "modification_notes": list(self.modification_notes),
            "context_fingerprint": self.context_fingerprint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidatePlan":
        _check_version(value)
        return cls(
            candidate_id=str(value["candidate_id"]),
            scenario_id=str(value["scenario_id"]),
            strategy_family=str(value["strategy_family"]),
            title=str(value["title"]),
            rationale=str(value["rationale"]),
            actions=tuple(TypedAction.from_dict(item) for item in value["actions"]),
            expected_observations=tuple(
                ExpectedObservation.from_dict(item)
                for item in value["expected_observations"]
            ),
            assumptions=tuple(str(item) for item in value.get("assumptions", [])),
            diversity_signature=tuple(
                str(item) for item in value.get("diversity_signature", [])
            ),
            source_episode_ids=tuple(
                str(item) for item in value.get("source_episode_ids", [])
            ),
            known_failure_mode_ids=tuple(
                str(item) for item in value.get("known_failure_mode_ids", [])
            ),
            status=CandidateStatus(
                str(value.get("status", CandidateStatus.PROPOSED.value))
            ),
            rejection_reasons=tuple(
                str(item) for item in value.get("rejection_reasons", [])
            ),
            modification_notes=tuple(
                str(item) for item in value.get("modification_notes", [])
            ),
            context_fingerprint=str(value.get("context_fingerprint", "")),
            created_at=str(value.get("created_at", utc_now())),
            updated_at=str(value.get("updated_at", utc_now())),
        )


@dataclass(frozen=True, slots=True)
class Prediction:
    prediction_id: str
    candidate_id: str
    observation_id: str
    claim: str
    expected_value: Any
    confidence: float
    provenance: Mapping[str, Any]
    assumptions: tuple[str, ...]
    causal_parents: tuple[str, ...]
    status: PredictionStatus = PredictionStatus.PROPOSED
    evidence_ids: tuple[str, ...] = ()
    simulation_run_id: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.prediction_id or not self.candidate_id or not self.observation_id:
            raise ValueError("prediction identity is required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("prediction confidence must be between 0 and 1")
        if not self.provenance:
            raise ValueError("prediction provenance is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "prediction_id": self.prediction_id,
            "candidate_id": self.candidate_id,
            "observation_id": self.observation_id,
            "claim": self.claim,
            "expected_value": self.expected_value,
            "confidence": self.confidence,
            "provenance": dict(self.provenance),
            "assumptions": list(self.assumptions),
            "causal_parents": list(self.causal_parents),
            "status": self.status.value,
            "evidence_ids": list(self.evidence_ids),
            "simulation_run_id": self.simulation_run_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Prediction":
        _check_version(value)
        return cls(
            prediction_id=str(value["prediction_id"]),
            candidate_id=str(value["candidate_id"]),
            observation_id=str(value["observation_id"]),
            claim=str(value["claim"]),
            expected_value=value.get("expected_value"),
            confidence=float(value["confidence"]),
            provenance=dict(value["provenance"]),
            assumptions=tuple(str(item) for item in value.get("assumptions", [])),
            causal_parents=tuple(str(item) for item in value.get("causal_parents", [])),
            status=PredictionStatus(
                str(value.get("status", PredictionStatus.PROPOSED.value))
            ),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", [])),
            simulation_run_id=value.get("simulation_run_id"),
            created_at=str(value.get("created_at", utc_now())),
        )


@dataclass(frozen=True, slots=True)
class SimulationRun:
    simulation_id: str
    candidate_id: str
    kind: SimulationKind
    status: SimulationStatus
    simulation_outcome: str
    evidence_scope: str
    findings: tuple[str, ...]
    prediction_ids: tuple[str, ...]
    assumptions: tuple[str, ...] = ()
    failure_summary: str | None = None
    real_effect: bool = False
    authority_created: bool = False
    workspace_path: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.evidence_scope != "simulation":
            raise ValueError("Phase 9 simulation evidence must be scoped as simulation")
        if self.real_effect or self.authority_created:
            raise ValueError("simulation cannot record a real effect or authority")
        if self.simulation_outcome not in {"success", "failure", "unknown"}:
            raise ValueError("simulation outcome must be success, failure, or unknown")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "simulation_id": self.simulation_id,
            "candidate_id": self.candidate_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "simulation_outcome": self.simulation_outcome,
            "evidence_scope": self.evidence_scope,
            "findings": list(self.findings),
            "prediction_ids": list(self.prediction_ids),
            "assumptions": list(self.assumptions),
            "failure_summary": self.failure_summary,
            "real_effect": self.real_effect,
            "authority_created": self.authority_created,
            "workspace_path": self.workspace_path,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SimulationRun":
        _check_version(value)
        return cls(
            simulation_id=str(value["simulation_id"]),
            candidate_id=str(value["candidate_id"]),
            kind=SimulationKind(str(value["kind"])),
            status=SimulationStatus(str(value["status"])),
            simulation_outcome=str(value["simulation_outcome"]),
            evidence_scope=str(value["evidence_scope"]),
            findings=tuple(str(item) for item in value.get("findings", [])),
            prediction_ids=tuple(str(item) for item in value.get("prediction_ids", [])),
            assumptions=tuple(str(item) for item in value.get("assumptions", [])),
            failure_summary=value.get("failure_summary"),
            real_effect=bool(value.get("real_effect", False)),
            authority_created=bool(value.get("authority_created", False)),
            workspace_path=value.get("workspace_path"),
            created_at=str(value.get("created_at", utc_now())),
        )


@dataclass(frozen=True, slots=True)
class FailureMode:
    failure_mode_id: str
    name: str
    description: str
    trigger: str
    evidence: tuple[str, ...]
    severity: str
    mitigations: tuple[str, ...]
    source_episode_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "failure_mode_id": self.failure_mode_id,
            "name": self.name,
            "description": self.description,
            "trigger": self.trigger,
            "evidence": list(self.evidence),
            "severity": self.severity,
            "mitigations": list(self.mitigations),
            "source_episode_ids": list(self.source_episode_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureMode":
        _check_version(value)
        return cls(
            failure_mode_id=str(value["failure_mode_id"]),
            name=str(value["name"]),
            description=str(value["description"]),
            trigger=str(value["trigger"]),
            evidence=tuple(str(item) for item in value.get("evidence", [])),
            severity=str(value["severity"]),
            mitigations=tuple(str(item) for item in value.get("mitigations", [])),
            source_episode_ids=tuple(
                str(item) for item in value.get("source_episode_ids", [])
            ),
        )


@dataclass(frozen=True, slots=True)
class CriticResult:
    critic_id: str
    candidate_id: str
    kind: CriticKind
    passed: bool
    severity: str
    findings: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    suggested_modifications: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "critic_id": self.critic_id,
            "candidate_id": self.candidate_id,
            "kind": self.kind.value,
            "passed": self.passed,
            "severity": self.severity,
            "findings": list(self.findings),
            "evidence_ids": list(self.evidence_ids),
            "suggested_modifications": list(self.suggested_modifications),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CriticResult":
        _check_version(value)
        return cls(
            critic_id=str(value["critic_id"]),
            candidate_id=str(value["candidate_id"]),
            kind=CriticKind(str(value["kind"])),
            passed=bool(value["passed"]),
            severity=str(value["severity"]),
            findings=tuple(str(item) for item in value.get("findings", [])),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", [])),
            suggested_modifications=tuple(
                str(item) for item in value.get("suggested_modifications", [])
            ),
            created_at=str(value.get("created_at", utc_now())),
        )


@dataclass(frozen=True, slots=True)
class Score:
    score_id: str
    candidate_id: str
    dimensions: Mapping[str, float]
    weighted_total: float
    rationale: str
    critic_ids: tuple[str, ...]
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        required = {item.value for item in ScoreDimension}
        missing = required - set(self.dimensions)
        if missing:
            raise ValueError(f"score is missing dimensions: {sorted(missing)}")
        if set(self.dimensions) != required:
            raise ValueError(
                "score dimensions must contain exactly the seven Phase 9 dimensions"
            )
        if any(not 0 <= float(value) <= 1 for value in self.dimensions.values()):
            raise ValueError("score dimensions must be between 0 and 1")
        if (
            not math.isfinite(float(self.weighted_total))
            or not 0 <= float(self.weighted_total) <= 1
        ):
            raise ValueError("weighted score must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "score_id": self.score_id,
            "candidate_id": self.candidate_id,
            "dimensions": dict(self.dimensions),
            "weighted_total": self.weighted_total,
            "rationale": self.rationale,
            "critic_ids": list(self.critic_ids),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Score":
        _check_version(value)
        return cls(
            score_id=str(value["score_id"]),
            candidate_id=str(value["candidate_id"]),
            dimensions={
                str(key): float(item) for key, item in dict(value["dimensions"]).items()
            },
            weighted_total=float(value["weighted_total"]),
            rationale=str(value["rationale"]),
            critic_ids=tuple(str(item) for item in value.get("critic_ids", [])),
            created_at=str(value.get("created_at", utc_now())),
        )


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    observation_id: str
    prediction_id: str
    observed_value: Any
    source_id: str
    observed_at: str
    status: PredictionStatus
    evidence_scope: str
    provenance: Mapping[str, Any]
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.observation_id or not self.prediction_id or not self.source_id:
            raise ValueError("observation identity and source are required")
        if self.evidence_scope not in {"simulation", "real_observation", "replay"}:
            raise ValueError("unsupported observation evidence scope")
        if self.status not in {
            PredictionStatus.VERIFIED,
            PredictionStatus.CONTRADICTED,
            PredictionStatus.UNKNOWN,
        }:
            raise ValueError(
                "observation status must be verified, contradicted, or unknown"
            )
        if not self.provenance:
            raise ValueError("observation provenance is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "prediction_id": self.prediction_id,
            "observed_value": self.observed_value,
            "source_id": self.source_id,
            "observed_at": self.observed_at,
            "status": self.status.value,
            "evidence_scope": self.evidence_scope,
            "provenance": dict(self.provenance),
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservationRecord":
        _check_version(value)
        return cls(
            observation_id=str(value["observation_id"]),
            prediction_id=str(value["prediction_id"]),
            observed_value=value.get("observed_value"),
            source_id=str(value["source_id"]),
            observed_at=str(value["observed_at"]),
            status=PredictionStatus(str(value["status"])),
            evidence_scope=str(value["evidence_scope"]),
            provenance=dict(value["provenance"]),
            details=dict(value.get("details", {})),
        )


@dataclass(frozen=True, slots=True)
class CalibrationRecord:
    calibration_id: str
    prediction_id: str
    expected_value: Any
    observed_value: Any
    result: PredictionStatus
    calibration_error: float
    evidence_ids: tuple[str, ...]
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.calibration_id or not self.prediction_id:
            raise ValueError("calibration identity is required")
        if self.result not in {
            PredictionStatus.VERIFIED,
            PredictionStatus.CONTRADICTED,
            PredictionStatus.UNKNOWN,
        }:
            raise ValueError(
                "calibration result must be verified, contradicted, or unknown"
            )
        if not self.evidence_ids:
            raise ValueError("calibration must retain evidence IDs")
        if (
            not math.isfinite(float(self.calibration_error))
            or not 0 <= float(self.calibration_error) <= 1
        ):
            raise ValueError("calibration error must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "calibration_id": self.calibration_id,
            "prediction_id": self.prediction_id,
            "expected_value": self.expected_value,
            "observed_value": self.observed_value,
            "result": self.result.value,
            "calibration_error": self.calibration_error,
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CalibrationRecord":
        _check_version(value)
        return cls(
            calibration_id=str(value["calibration_id"]),
            prediction_id=str(value["prediction_id"]),
            expected_value=value.get("expected_value"),
            observed_value=value.get("observed_value"),
            result=PredictionStatus(str(value["result"])),
            calibration_error=float(value["calibration_error"]),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", [])),
            created_at=str(value.get("created_at", utc_now())),
        )


@dataclass(frozen=True, slots=True)
class ImaginationDecision:
    decision_id: str
    scenario_id: str
    candidate_ids: tuple[str, ...]
    valid_candidate_ids: tuple[str, ...]
    rejected_candidate_ids: tuple[str, ...]
    selected_candidate_id: str | None
    reason: str
    predicted_observations: tuple[str, ...]
    failure_mode_ids: tuple[str, ...]
    critic_ids: tuple[str, ...]
    score_ids: tuple[str, ...]
    guardian_candidate_ids: tuple[str, ...]
    authority_created: bool = False
    live_effects: bool = False
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.authority_created or self.live_effects:
            raise ValueError(
                "imagination decisions cannot create authority or live effects"
            )
        candidate_ids = set(self.candidate_ids)
        valid_ids = set(self.valid_candidate_ids)
        rejected_ids = set(self.rejected_candidate_ids)
        if len(candidate_ids) != len(self.candidate_ids):
            raise ValueError("decision candidate IDs must be unique")
        if not valid_ids <= candidate_ids or not rejected_ids <= candidate_ids:
            raise ValueError("decision subsets must refer to candidate IDs")
        if valid_ids & rejected_ids:
            raise ValueError("a candidate cannot be both valid and rejected")
        if (
            self.selected_candidate_id is not None
            and self.selected_candidate_id not in valid_ids
        ):
            raise ValueError("selected candidate must be valid")
        if not set(self.guardian_candidate_ids) <= valid_ids:
            raise ValueError("only valid candidates may be sent to Guardian")
        if len(set(self.guardian_candidate_ids)) != len(self.guardian_candidate_ids):
            raise ValueError("Guardian candidate IDs must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE9_SCHEMA_VERSION,
            "decision_id": self.decision_id,
            "scenario_id": self.scenario_id,
            "candidate_ids": list(self.candidate_ids),
            "valid_candidate_ids": list(self.valid_candidate_ids),
            "rejected_candidate_ids": list(self.rejected_candidate_ids),
            "selected_candidate_id": self.selected_candidate_id,
            "reason": self.reason,
            "predicted_observations": list(self.predicted_observations),
            "failure_mode_ids": list(self.failure_mode_ids),
            "critic_ids": list(self.critic_ids),
            "score_ids": list(self.score_ids),
            "guardian_candidate_ids": list(self.guardian_candidate_ids),
            "authority_created": self.authority_created,
            "live_effects": self.live_effects,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImaginationDecision":
        _check_version(value)
        return cls(
            decision_id=str(value["decision_id"]),
            scenario_id=str(value["scenario_id"]),
            candidate_ids=tuple(str(item) for item in value.get("candidate_ids", [])),
            valid_candidate_ids=tuple(
                str(item) for item in value.get("valid_candidate_ids", [])
            ),
            rejected_candidate_ids=tuple(
                str(item) for item in value.get("rejected_candidate_ids", [])
            ),
            selected_candidate_id=value.get("selected_candidate_id"),
            reason=str(value["reason"]),
            predicted_observations=tuple(
                str(item) for item in value.get("predicted_observations", [])
            ),
            failure_mode_ids=tuple(
                str(item) for item in value.get("failure_mode_ids", [])
            ),
            critic_ids=tuple(str(item) for item in value.get("critic_ids", [])),
            score_ids=tuple(str(item) for item in value.get("score_ids", [])),
            guardian_candidate_ids=tuple(
                str(item) for item in value.get("guardian_candidate_ids", [])
            ),
            authority_created=bool(value.get("authority_created", False)),
            live_effects=bool(value.get("live_effects", False)),
            created_at=str(value.get("created_at", utc_now())),
        )


def _check_version(value: Mapping[str, Any]) -> None:
    version = int(value.get("schema_version", PHASE9_SCHEMA_VERSION))
    if version != PHASE9_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported Phase 9 schema version {version}; "
            f"supported version is {PHASE9_SCHEMA_VERSION}"
        )


__all__ = [
    "CalibrationRecord",
    "CandidatePlan",
    "CandidateStatus",
    "CriticKind",
    "CriticResult",
    "ExpectedObservation",
    "FailureMode",
    "ImaginationDecision",
    "ObservationRecord",
    "PHASE9_SCHEMA_VERSION",
    "Prediction",
    "PredictionStatus",
    "Score",
    "ScoreDimension",
    "SimulationKind",
    "SimulationRun",
    "SimulationStatus",
    "TypedAction",
    "canonical_json",
    "stable_id",
    "utc_now",
]
