"""Typed, provenance-preserving contracts for the Phase 10 Learning Laboratory.

The laboratory deliberately uses a separate contract family from the older
learning optimizers.  A learning record is evidence about a possible change;
it is not a production configuration change or a Guardian authorization.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: Any) -> str:
    raw = json.dumps(
        _jsonable(parts), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class CandidateStatus(str, Enum):
    CANDIDATE = "candidate"
    SANDBOXED = "sandboxed"
    REPLAY_PASSED = "replay_passed"
    SHADOW = "shadow"
    APPROVED = "approved"
    STAGED = "staged"
    ACTIVE = "active"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class CandidateKind(str, Enum):
    PROCEDURE = "procedure"
    RETRIEVAL = "retrieval"
    ROUTING = "routing"
    PROMPT = "prompt"
    THRESHOLD = "threshold"
    POLICY_PACK = "policy_pack"
    MODEL_ADAPTER = "model_adapter"
    CODEX_MISSION_TEMPLATE = "codex_mission_template"


class ReplayStatus(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    CANCELED = "canceled"


class ShadowStatus(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class ArtifactStage(str, Enum):
    STAGED = "staged"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"


SAFE_ACTIVATION_SCOPE = {
    "environment": "deterministic-fixture",
    "live_external_effects": False,
    "guardian_authority": False,
}


@dataclass
class LaboratoryContract:
    """Common versioned envelope for every durable Phase 10 record."""

    contract_type: ClassVar[str] = "laboratory_contract"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)
    valid_from: str = field(default_factory=utc_now)
    valid_until: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    causal_parents: list[str] = field(default_factory=list)
    sensitivity_labels: list[str] = field(default_factory=list)
    taint_labels: list[str] = field(default_factory=list)
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("laboratory contract id must not be empty")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported Phase 10 schema version {self.schema_version}; "
                f"supported version is {SCHEMA_VERSION}"
            )
        for name in ("created_at", "valid_from"):
            try:
                datetime.fromisoformat(getattr(self, name))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be ISO-8601") from exc
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {"contract_type": self.contract_type, **_jsonable(asdict(self))}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LaboratoryContract":
        return contract_from_dict(raw, expected=cls)

    @classmethod
    def from_json(cls, value: str) -> "LaboratoryContract":
        raw = json.loads(value)
        if not isinstance(raw, dict):
            raise ValueError("laboratory contract JSON root must be an object")
        return cls.from_dict(raw)


@dataclass
class Correction(LaboratoryContract):
    contract_type: ClassVar[str] = "correction"
    statement: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    source: str = "marc"
    evidence_kinds: list[str] = field(
        default_factory=lambda: ["relationship", "procedural"]
    )
    source_ids: list[str] = field(default_factory=list)
    sensitivity: str = "personal"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.statement.strip():
            raise ValueError("correction statement must not be empty")
        required = {"relationship", "procedural"}
        supplied = set(self.evidence_kinds)
        if supplied != required or len(self.evidence_kinds) != len(supplied):
            raise ValueError(
                "correction must contain exactly one relationship and one "
                "procedural evidence kind"
            )


@dataclass
class MemoryEvidence(LaboratoryContract):
    contract_type: ClassVar[str] = "memory_evidence"
    correction_id: str = ""
    evidence_kind: str = "relationship"
    statement: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    durable: bool = True
    source: str = "marc"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.correction_id:
            raise ValueError("memory evidence must reference a correction")
        if self.evidence_kind not in {"relationship", "procedural"}:
            raise ValueError(f"unsupported memory evidence kind {self.evidence_kind!r}")
        if not self.statement.strip():
            raise ValueError("memory evidence statement must not be empty")
        if not self.durable:
            raise ValueError("Phase 10 memory evidence must be durable")


@dataclass
class ImmediateAdaptation(LaboratoryContract):
    contract_type: ClassVar[str] = "immediate_adaptation"
    correction_id: str = ""
    relationship_evidence_id: str = ""
    procedural_evidence_id: str = ""
    belief_revision: str = ""
    current_plan_revision: str = ""
    production_weights_changed: bool = False
    production_prompts_changed: bool = False
    production_policies_changed: bool = False
    production_skills_changed: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.correction_id:
            raise ValueError("adaptation must reference a correction")
        if not self.relationship_evidence_id or not self.procedural_evidence_id:
            raise ValueError("adaptation must reference both evidence kinds")
        if not self.belief_revision.strip() or not self.current_plan_revision.strip():
            raise ValueError("adaptation must record belief and plan revisions")
        if any(
            (
                self.production_weights_changed,
                self.production_prompts_changed,
                self.production_policies_changed,
                self.production_skills_changed,
            )
        ):
            raise ValueError("immediate adaptation cannot mutate production")


@dataclass
class Procedure(LaboratoryContract):
    contract_type: ClassVar[str] = "procedure"
    name: str = ""
    trigger: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)
    source_correction_ids: list[str] = field(default_factory=list)
    version: str = "1"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.name.strip() or not self.source_correction_ids:
            raise ValueError("procedure needs a name and correction provenance")
        if not self.trigger or not self.steps:
            raise ValueError("procedure needs a trigger and at least one step")


@dataclass
class LearningCandidate(LaboratoryContract):
    contract_type: ClassVar[str] = "learning_candidate"
    candidate_kind: str = CandidateKind.PROCEDURE.value
    status: str = CandidateStatus.CANDIDATE.value
    procedure_id: str | None = None
    source_correction_ids: list[str] = field(default_factory=list)
    source_episode_ids: list[str] = field(default_factory=list)
    source_trace_ids: list[str] = field(default_factory=list)
    source_version: str = "v1"
    named_outcome: str = ""
    proposal: dict[str, Any] = field(default_factory=dict)
    baseline_behavior: dict[str, Any] = field(default_factory=dict)
    expected_behavior: dict[str, Any] = field(default_factory=dict)
    required_marc_approval: bool = True
    authority_scope: dict[str, Any] = field(default_factory=dict)
    reversible: bool = True
    privacy_class: str = "personal"
    consent_id: str | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    replay_run_ids: list[str] = field(default_factory=list)
    shadow_run_ids: list[str] = field(default_factory=list)
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.status not in {item.value for item in CandidateStatus}:
            raise ValueError(f"unsupported candidate status {self.status!r}")
        if self.candidate_kind not in {item.value for item in CandidateKind}:
            raise ValueError(f"unsupported candidate kind {self.candidate_kind!r}")
        if not self.named_outcome.strip():
            raise ValueError("learning candidate must name an outcome")
        if not isinstance(self.proposal, dict):
            raise ValueError("learning candidate proposal must be an object")
        if not isinstance(self.baseline_behavior, dict) or not isinstance(
            self.expected_behavior, dict
        ):
            raise ValueError("candidate behavior contracts must be objects")
        if not self.source_version.strip():
            raise ValueError("candidate source version must not be empty")
        if self.authority_scope:
            raise ValueError("learning candidates cannot carry Guardian authority")


@dataclass
class ReplayCase(LaboratoryContract):
    contract_type: ClassVar[str] = "replay_case"
    suite: str = ""
    name: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    source_version: str = "v1"
    budget_cost: int = 1
    sensitive_source_ids: list[str] = field(default_factory=list)
    canceled: bool = False
    disagreement: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.suite.strip() or not self.name.strip():
            raise ValueError("replay case needs a suite and name")
        if not isinstance(self.context, dict) or not isinstance(self.expected, dict):
            raise ValueError("replay case context and expected result must be objects")
        if self.budget_cost < 1:
            raise ValueError("replay case budget cost must be positive")
        if not self.source_version.strip():
            raise ValueError("replay case source version must not be empty")


@dataclass
class ReplayRun(LaboratoryContract):
    contract_type: ClassVar[str] = "replay_run"
    candidate_id: str = ""
    suite: str = ""
    mode: str = "deterministic"
    status: str = ReplayStatus.RUNNING.value
    case_results: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    passed: bool = False
    adversarial_passed: bool = False
    counterfactual_passed: bool = False
    privacy_compliant: bool = True
    consent_compliant: bool = True
    protected_capability_regressions: list[str] = field(default_factory=list)
    forgetting_regressions: list[str] = field(default_factory=list)
    unauthorized_actions: int = 0
    duplicate_actions: int = 0
    stale_source: bool = False
    disagreement: bool = False
    budget_exceeded: bool = False
    canceled: bool = False
    source_digest: str = ""
    evidence_scope: str = "simulation"
    real_effect: bool = False
    authority_created: bool = False
    external_effects: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.status not in {item.value for item in ReplayStatus}:
            raise ValueError(f"unsupported replay status {self.status!r}")
        if self.evidence_scope != "simulation":
            raise ValueError("Phase 10 replay evidence must be simulation-scoped")
        if self.real_effect or self.authority_created or self.external_effects:
            raise ValueError("replay runs must be effect-free and authority-free")
        if self.status == ReplayStatus.PASSED.value and not self.passed:
            raise ValueError("passed replay runs must record passed=true")
        if self.status == ReplayStatus.RUNNING.value and self.passed:
            raise ValueError("running replay runs cannot record passed=true")
        if self.status == ReplayStatus.CANCELED.value and not self.canceled:
            raise ValueError("canceled replay runs must record canceled=true")


@dataclass
class ShadowRun(LaboratoryContract):
    contract_type: ClassVar[str] = "shadow_run"
    candidate_id: str = ""
    suite: str = ""
    status: str = ShadowStatus.RUNNING.value
    case_results: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = False
    real_effect: bool = False
    authority_created: bool = False
    external_effects: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    baseline_digest: str = ""
    candidate_digest: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.status not in {item.value for item in ShadowStatus}:
            raise ValueError(f"unsupported shadow status {self.status!r}")
        if self.real_effect or self.authority_created or self.external_effects:
            raise ValueError("shadow runs must be effect-free and authority-free")
        if self.status == ShadowStatus.PASSED.value and not self.passed:
            raise ValueError("passed shadow runs must record passed=true")
        if self.status == ShadowStatus.RUNNING.value and self.passed:
            raise ValueError("running shadow runs cannot record passed=true")


@dataclass
class PromotionDecision(LaboratoryContract):
    contract_type: ClassVar[str] = "promotion_decision"
    candidate_id: str = ""
    decision: str = "approved"
    reviewer: str = ""
    explicit_marc_approval: bool = False
    named_outcome: str = ""
    improvement: float = 0.0
    requirements: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    rollback_verified: bool = False
    authority_created: bool = False


@dataclass
class ArtifactVersion(LaboratoryContract):
    contract_type: ClassVar[str] = "artifact_version"
    candidate_id: str = ""
    artifact_kind: str = ""
    artifact_name: str = ""
    version: str = ""
    digest: str = ""
    signature: str = ""
    signed_by: str = ""
    stage: str = ArtifactStage.STAGED.value
    previous_artifact_id: str | None = None
    activation_scope: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.stage not in {item.value for item in ArtifactStage}:
            raise ValueError(f"unsupported artifact stage {self.stage!r}")
        if not self.signature or not self.digest:
            raise ValueError("artifact version must be signed and content-addressed")
        if self.activation_scope != SAFE_ACTIVATION_SCOPE:
            raise ValueError("Phase 10 artifacts must remain fixture-only")


@dataclass
class Consent(LaboratoryContract):
    contract_type: ClassVar[str] = "consent"
    subject: str = "Marc"
    purpose: str = ""
    source_ids: list[str] = field(default_factory=list)
    allowed_uses: list[str] = field(default_factory=list)
    sensitivity_ceiling: str = "personal"
    explicit: bool = False
    privacy_reviewed: bool = False
    reviewer: str = ""
    revoked: bool = False
    expires_at: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.explicit and not self.purpose.strip():
            raise ValueError("explicit consent must state a purpose")


@dataclass
class RegressionResult(LaboratoryContract):
    contract_type: ClassVar[str] = "regression_result"
    candidate_id: str = ""
    suite: str = ""
    protected_capabilities: list[str] = field(default_factory=list)
    forgetting_cases: list[str] = field(default_factory=list)
    behavioral_regressions: list[str] = field(default_factory=list)
    unauthorized_actions: int = 0
    duplicate_actions: int = 0
    passed: bool = False


@dataclass
class RollbackRecord(LaboratoryContract):
    contract_type: ClassVar[str] = "rollback_record"
    candidate_id: str = ""
    artifact_id: str = ""
    restored_artifact_id: str | None = None
    reason: str = ""
    exact_restore: bool = False
    rollback_verified: bool = False
    authority_scope_restored: bool = False
    restored_behavior_digest: str = ""


@dataclass
class RuntimeState(LaboratoryContract):
    contract_type: ClassVar[str] = "runtime_state"
    active_artifact_id: str | None = None
    active_candidate_id: str | None = None
    active_behavior_digest: str = ""
    baseline_behavior: dict[str, Any] = field(default_factory=dict)


CONTRACT_TYPES = {
    item.contract_type: item
    for item in (
        Correction,
        MemoryEvidence,
        ImmediateAdaptation,
        Procedure,
        LearningCandidate,
        ReplayCase,
        ReplayRun,
        ShadowRun,
        PromotionDecision,
        ArtifactVersion,
        Consent,
        RegressionResult,
        RollbackRecord,
        RuntimeState,
    )
}


def contract_from_dict(
    raw: dict[str, Any], *, expected: type[LaboratoryContract] | None = None
) -> LaboratoryContract:
    if not isinstance(raw, dict):
        raise ValueError("laboratory contract must be an object")
    value = dict(raw)
    contract_type = value.pop("contract_type", None)
    target = CONTRACT_TYPES.get(contract_type)
    if target is None:
        raise ValueError(f"unknown Phase 10 contract type {contract_type!r}")
    if (
        expected is not None
        and expected is not LaboratoryContract
        and target is not expected
    ):
        raise ValueError(f"expected {expected.contract_type}, got {contract_type}")
    if value.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported Phase 10 schema version {value.get('schema_version')!r}"
        )
    value.setdefault("schema_version", SCHEMA_VERSION)
    allowed = {item.name for item in fields(target)}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown fields for {contract_type}: {sorted(unknown)}")
    return target(**value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "ArtifactStage",
    "ArtifactVersion",
    "CandidateKind",
    "CandidateStatus",
    "Consent",
    "Correction",
    "ImmediateAdaptation",
    "LaboratoryContract",
    "LearningCandidate",
    "MemoryEvidence",
    "Procedure",
    "PromotionDecision",
    "RegressionResult",
    "ReplayCase",
    "ReplayRun",
    "ReplayStatus",
    "RollbackRecord",
    "RuntimeState",
    "ShadowRun",
    "ShadowStatus",
    "SAFE_ACTIVATION_SCOPE",
    "contract_from_dict",
    "stable_id",
    "utc_now",
]
