"""Typed, model-independent contracts for the Phase 8 executive.

The executive is deliberately a small durable coordination layer.  It keeps
goals, claims, artifacts, and decisions separate from model transcripts so a
process restart can recover the mission without replaying completed work.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, ClassVar, Optional, Type, TypeVar

from openjarvis.cognition.models import CognitionContract


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class AssignmentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    BLOCKED = "blocked"


class ClaimStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class CommitmentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"
    COMPLETED = "completed"


class CouncilStatus(str, Enum):
    FORMING = "forming"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"


class SpecialistRole(str, Enum):
    PERSONAL_CONTEXT = "personal-context-memory-researcher"
    PLANNING = "planning-scheduling-specialist"
    SOFTWARE = "software-codex-specialist"
    HOME_SYSTEMS = "home-systems-specialist"
    SECURITY = "security-privacy-specialist"
    EVIDENCE_CRITIC = "evidence-critic"
    PLAN_CRITIC = "plan-failure-mode-critic"
    COMMUNICATION = "communication-specialist"


class ExecutiveContract(CognitionContract):
    """Common deserialization entry point for Phase 8 contracts."""

    contract_type: ClassVar[str] = "executive_contract"

    @classmethod
    def from_dict(
        cls: Type["ExecutiveContract"], value: dict[str, Any]
    ) -> "ExecutiveContract":
        raw = dict(value)
        discriminator = raw.pop("contract_type", None)
        target = EXECUTIVE_CONTRACT_TYPES.get(discriminator)
        if target is None:
            raise ValueError(f"unknown Phase 8 contract type: {discriminator!r}")
        if cls is not ExecutiveContract and target is not cls:
            raise ValueError(f"expected {cls.contract_type}, got {discriminator}")
        allowed = {item.name for item in fields(target)}
        extra = set(raw) - allowed
        if extra:
            raise ValueError(f"unsupported fields for {discriminator}: {sorted(extra)}")
        return target(**raw)


@dataclass
class ExecutiveGoal(ExecutiveContract):
    contract_type: ClassVar[str] = "executive_goal"
    objective: str = ""
    success_conditions: list[str] = field(default_factory=list)
    priority: int = 50
    owner: str = "Ophanim"
    deadline: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)
    parent_goal_id: Optional[str] = None
    status: GoalStatus = GoalStatus.ACTIVE
    kind: str = "goal"
    completed_at: Optional[str] = None
    status_reason: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.objective.strip():
            raise ValueError("goal objective must not be empty")
        if not self.success_conditions:
            raise ValueError("goal must declare at least one success condition")
        if not 0 <= self.priority <= 100:
            raise ValueError("goal priority must be between 0 and 100")
        self.status = GoalStatus(self.status)
        if self.kind not in {"goal", "subgoal"}:
            raise ValueError("goal kind must be goal or subgoal")


@dataclass
class ExecutiveCommitment(ExecutiveContract):
    contract_type: ClassVar[str] = "executive_commitment"
    goal_id: str = ""
    promise: str = ""
    made_to: str = "Marc"
    owner: str = "Ophanim"
    due_at: Optional[str] = None
    success_conditions: list[str] = field(default_factory=list)
    status: CommitmentStatus = CommitmentStatus.ACTIVE
    supersedes_id: Optional[str] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.goal_id or not self.promise.strip():
            raise ValueError("commitment requires goal_id and promise")
        self.status = CommitmentStatus(self.status)


@dataclass
class GlobalWorkspace(ExecutiveContract):
    contract_type: ClassVar[str] = "global_workspace"
    goal_id: str = ""
    current_focus: str = ""
    relevant_observations: list[dict[str, Any]] = field(default_factory=list)
    beliefs: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    candidate_plans: list[dict[str, Any]] = field(default_factory=list)
    specialist_claim_ids: list[str] = field(default_factory=list)
    guardian_feedback: list[dict[str, Any]] = field(default_factory=list)
    revision: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.goal_id:
            raise ValueError("workspace requires goal_id")
        if self.revision < 0:
            raise ValueError("workspace revision cannot be negative")
        for name in (
            "relevant_observations",
            "beliefs",
            "constraints",
            "unresolved_questions",
            "candidate_plans",
            "specialist_claim_ids",
            "guardian_feedback",
        ):
            if len(getattr(self, name)) > 64:
                raise ValueError(f"workspace field {name} exceeds compact limit")


@dataclass
class SpecialistClaim(ExecutiveContract):
    contract_type: ClassVar[str] = "specialist_claim"
    goal_id: str = ""
    specialist_role: str = ""
    statement: str = ""
    confidence: Optional[float] = 0.5
    evidence_ids: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    supports_claim_ids: list[str] = field(default_factory=list)
    contradicts_claim_ids: list[str] = field(default_factory=list)
    status: ClaimStatus = ClaimStatus.PROPOSED
    missing_information: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.goal_id or not self.specialist_role or not self.statement.strip():
            raise ValueError("claim requires goal_id, specialist_role, and statement")
        if self.confidence is None:
            raise ValueError("every specialist claim must retain confidence")
        if not self.evidence_ids and not self.evidence:
            raise ValueError("every specialist claim must retain evidence")
        self.status = ClaimStatus(self.status)


@dataclass
class SpecialistAssignment(ExecutiveContract):
    contract_type: ClassVar[str] = "specialist_assignment"
    goal_id: str = ""
    council_id: str = ""
    subgoal_id: Optional[str] = None
    specialist_role: str = ""
    objective: str = ""
    budget: dict[str, int] = field(default_factory=dict)
    allowed_capabilities: list[str] = field(default_factory=list)
    mission_template: Optional[str] = None
    mission_id: Optional[str] = None
    claim_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    status: AssignmentStatus = AssignmentStatus.PENDING
    result_summary: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.goal_id or not self.council_id or not self.specialist_role:
            raise ValueError("assignment requires goal_id, council_id, and role")
        if not self.objective.strip():
            raise ValueError("assignment objective must not be empty")
        self.status = AssignmentStatus(self.status)
        if any(value < 0 for value in self.budget.values()):
            raise ValueError("assignment budgets cannot be negative")


@dataclass
class SpecialistCouncil(ExecutiveContract):
    contract_type: ClassVar[str] = "specialist_council"
    goal_id: str = ""
    specialist_roles: list[str] = field(default_factory=list)
    assignment_ids: list[str] = field(default_factory=list)
    status: CouncilStatus = CouncilStatus.FORMING
    budget: dict[str, int] = field(default_factory=dict)
    disagreement_ids: list[str] = field(default_factory=list)
    synthesis_owner: str = "Executive"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.goal_id or not self.specialist_roles:
            raise ValueError("council requires a goal and at least one specialist")
        self.status = CouncilStatus(self.status)


@dataclass
class VerifiedArtifact(ExecutiveContract):
    contract_type: ClassVar[str] = "verified_artifact"
    goal_id: str = ""
    artifact_kind: str = ""
    path: str = ""
    checksum: str = ""
    verification_method: str = ""
    verified: bool = False
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.goal_id or not self.artifact_kind or not self.path:
            raise ValueError("artifact requires goal_id, kind, and path")
        if self.verified and not self.checksum:
            raise ValueError("verified artifact requires a checksum")
        if not self.evidence_ids:
            raise ValueError("artifact requires evidence ids")


@dataclass
class ExecutiveDecisionReceipt(ExecutiveContract):
    contract_type: ClassVar[str] = "executive_decision_receipt"
    goal_id: str = ""
    decision: str = ""
    rationale: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    disagreement_ids: list[str] = field(default_factory=list)
    verified_artifact_ids: list[str] = field(default_factory=list)
    guardian_boundary: str = (
        "Guardian remains sole authority for consequential effects."
    )
    baseline_comparison: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    outcome: str = "proposed"
    narrative: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.goal_id or not self.decision.strip() or not self.rationale.strip():
            raise ValueError("decision receipt requires goal, decision, and rationale")
        if not self.evidence_ids:
            raise ValueError("decision receipt requires evidence")
        if self.verified and not self.verified_artifact_ids:
            raise ValueError("verified decision requires a verified artifact")


EXECUTIVE_CONTRACT_TYPES: dict[str, Type[ExecutiveContract]] = {
    item.contract_type: item
    for item in (
        ExecutiveGoal,
        ExecutiveCommitment,
        GlobalWorkspace,
        SpecialistClaim,
        SpecialistAssignment,
        SpecialistCouncil,
        VerifiedArtifact,
        ExecutiveDecisionReceipt,
    )
}


T = TypeVar("T", bound=ExecutiveContract)


def contract_from_json(payload: str) -> ExecutiveContract:
    import json

    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Phase 8 contract JSON must contain an object")
    return ExecutiveContract.from_dict(value)


__all__ = [
    "AssignmentStatus",
    "ClaimStatus",
    "CommitmentStatus",
    "CouncilStatus",
    "ExecutiveCommitment",
    "ExecutiveContract",
    "ExecutiveDecisionReceipt",
    "ExecutiveGoal",
    "GlobalWorkspace",
    "GoalStatus",
    "SpecialistAssignment",
    "SpecialistClaim",
    "SpecialistCouncil",
    "SpecialistRole",
    "VerifiedArtifact",
    "contract_from_json",
]
