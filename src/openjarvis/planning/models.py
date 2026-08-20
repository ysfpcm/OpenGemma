"""Typed, deterministic contracts for Phase 6 planning and authorization.

The objects in this module are deliberately separate from the existing
``cognition.Plan`` contract.  Phase 0--5 callers can continue to use that
small compatibility contract while Phase 6 has the richer, reviewable model
it needs.  A structured plan contains only ``ActionProposal`` instances;
free-form text is explanatory metadata and is never interpreted as authority.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from openjarvis.cognition import ActionProposal, Authorization


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _parse_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamps must be ISO-8601 values") from exc
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _require_bool(value: Any, name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")


def _require_finite_number(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")


class PlanStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALID = "invalid"
    EXPIRED = "expired"
    CANCELED = "canceled"


class AutonomyLevel(int, Enum):
    SHADOW = 0
    SUGGEST = 1
    APPROVE = 2
    DELEGATED = 3
    MATURE_ROUTINE = 4


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    """One typed input used by planning; text is evidence, not instruction."""

    key: str
    observed_at: str
    satisfied: bool
    confidence: float = 1.0
    source_id: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.key or not self.source_id:
            raise ValueError("observation key and source_id are required")
        _require_bool(self.satisfied, "observation satisfaction")
        _parse_timestamp(self.observed_at)
        _require_finite_number(self.confidence, "observation confidence")
        if not 0 <= self.confidence <= 1:
            raise ValueError("observation confidence must be between 0 and 1")

    def age_seconds(self, now: datetime) -> float:
        return max(0.0, (now - _parse_timestamp(self.observed_at)).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "observed_at": self.observed_at,
            "satisfied": self.satisfied,
            "confidence": self.confidence,
            "source_id": self.source_id,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservationSnapshot":
        allowed = {
            "key",
            "observed_at",
            "satisfied",
            "confidence",
            "source_id",
            "details",
        }
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"unknown observation fields: {sorted(extra)}")
        return cls(
            key=str(value["key"]),
            observed_at=str(value["observed_at"]),
            satisfied=value["satisfied"],
            confidence=float(value.get("confidence", 1.0)),
            source_id=str(value["source_id"]),
            details=dict(value.get("details", {})),
        )


@dataclass(frozen=True, slots=True)
class PlanningContext:
    """The complete context against which a plan or grant is evaluated."""

    now: str
    situation_id: str
    situation_type: str
    workspace: str
    location: str
    household_mode: str
    observations: Mapping[str, ObservationSnapshot]
    calendar_event_id: str | None = None
    calendar_event_start: str | None = None
    presence: tuple[str, ...] = ()
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.situation_id or not self.situation_type:
            raise ValueError("situation identity is required")
        if not self.workspace or not self.location or not self.household_mode:
            raise ValueError("workspace, location, and household_mode are required")
        _parse_timestamp(self.now)
        if self.calendar_event_start is not None:
            _parse_timestamp(self.calendar_event_start)
        _require_finite_number(self.confidence, "context confidence")
        if not 0 <= self.confidence <= 1:
            raise ValueError("context confidence must be between 0 and 1")
        if set(self.observations) != {item.key for item in self.observations.values()}:
            raise ValueError("observation keys must match their snapshot keys")

    @property
    def now_datetime(self) -> datetime:
        return _parse_timestamp(self.now)

    def identity(self) -> dict[str, Any]:
        return {
            "situation_id": self.situation_id,
            "situation_type": self.situation_type,
            "workspace": self.workspace,
            "location": self.location,
            "household_mode": self.household_mode,
            "calendar_event_id": self.calendar_event_id,
            "calendar_event_start": self.calendar_event_start,
            "presence": list(self.presence),
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(_json(self.identity()).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "now": self.now,
            **self.identity(),
            "observations": {
                key: value.to_dict() for key, value in sorted(self.observations.items())
            },
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlanningContext":
        allowed = {
            "now",
            "situation_id",
            "situation_type",
            "workspace",
            "location",
            "household_mode",
            "observations",
            "calendar_event_id",
            "calendar_event_start",
            "presence",
            "confidence",
        }
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"unknown planning context fields: {sorted(extra)}")
        observations = {
            str(key): ObservationSnapshot.from_dict(item)
            for key, item in dict(value.get("observations", {})).items()
        }
        return cls(
            now=str(value["now"]),
            situation_id=str(value["situation_id"]),
            situation_type=str(value["situation_type"]),
            workspace=str(value["workspace"]),
            location=str(value["location"]),
            household_mode=str(value["household_mode"]),
            observations=observations,
            calendar_event_id=value.get("calendar_event_id"),
            calendar_event_start=value.get("calendar_event_start"),
            presence=tuple(str(item) for item in value.get("presence", [])),
            confidence=float(value.get("confidence", 1.0)),
        )


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    observation_key: str
    expected: bool = True
    max_age_seconds: int = 300
    min_confidence: float = 0.0

    def __post_init__(self) -> None:
        if not self.name or not self.observation_key:
            raise ValueError("condition name and observation key are required")
        _require_bool(self.expected, "condition expectation")
        _require_finite_number(self.min_confidence, "condition confidence")
        if self.max_age_seconds < 0 or not 0 <= self.min_confidence <= 1:
            raise ValueError("invalid condition freshness or confidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "observation_key": self.observation_key,
            "expected": self.expected,
            "max_age_seconds": self.max_age_seconds,
            "min_confidence": self.min_confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Condition":
        allowed = {
            "name",
            "observation_key",
            "expected",
            "max_age_seconds",
            "min_confidence",
        }
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"unknown condition fields: {sorted(extra)}")
        return cls(
            name=str(value["name"]),
            observation_key=str(value["observation_key"]),
            expected=value.get("expected", True),
            max_age_seconds=int(value.get("max_age_seconds", 300)),
            min_confidence=float(value.get("min_confidence", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class ExpectedObservation:
    name: str
    observation_key: str
    description: str

    def __post_init__(self) -> None:
        if not self.name or not self.observation_key or not self.description:
            raise ValueError("expected observations require name, key, and description")

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "observation_key": self.observation_key,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExpectedObservation":
        allowed = {"name", "observation_key", "description"}
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"unknown expected observation fields: {sorted(extra)}")
        return cls(
            name=str(value["name"]),
            observation_key=str(value["observation_key"]),
            description=str(value["description"]),
        )


@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    proposal: ActionProposal
    executor: str
    capability: str
    consequence_class: str
    reversible: bool
    dependencies: tuple[str, ...]
    preconditions: tuple[Condition, ...]
    expected_observations: tuple[ExpectedObservation, ...]
    expires_at: str
    rationale: str
    cancellation_conditions: tuple[Condition, ...]
    resource_cost: float = 0.0
    requires_explicit_approval: bool = False

    def __post_init__(self) -> None:
        if not self.step_id or not self.executor or not self.capability:
            raise ValueError("step identity, executor, and capability are required")
        if not self.consequence_class or not self.rationale:
            raise ValueError("step consequence class and rationale are required")
        _require_bool(self.reversible, "step reversibility")
        _require_bool(self.requires_explicit_approval, "step approval requirement")
        _require_finite_number(self.resource_cost, "step resource cost")
        if self.resource_cost < 0:
            raise ValueError("step resource cost must not be negative")
        _parse_timestamp(self.expires_at)
        if not isinstance(self.proposal, ActionProposal):
            raise TypeError("structured plans may contain only ActionProposal objects")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "proposal": self.proposal.to_dict(),
            "executor": self.executor,
            "capability": self.capability,
            "consequence_class": self.consequence_class,
            "reversible": self.reversible,
            "dependencies": list(self.dependencies),
            "preconditions": [item.to_dict() for item in self.preconditions],
            "expected_observations": [
                item.to_dict() for item in self.expected_observations
            ],
            "expires_at": self.expires_at,
            "rationale": self.rationale,
            "cancellation_conditions": [
                item.to_dict() for item in self.cancellation_conditions
            ],
            "resource_cost": self.resource_cost,
            "requires_explicit_approval": self.requires_explicit_approval,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlanStep":
        allowed = {
            "step_id",
            "proposal",
            "executor",
            "capability",
            "consequence_class",
            "reversible",
            "dependencies",
            "preconditions",
            "expected_observations",
            "expires_at",
            "rationale",
            "cancellation_conditions",
            "resource_cost",
            "requires_explicit_approval",
        }
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"unknown plan step fields: {sorted(extra)}")
        proposal = value.get("proposal")
        if not isinstance(proposal, Mapping):
            raise ValueError("plan step proposal must be an ActionProposal object")
        parsed_proposal = ActionProposal.from_dict(dict(proposal))
        return cls(
            step_id=str(value["step_id"]),
            proposal=parsed_proposal,
            executor=str(value["executor"]),
            capability=str(value["capability"]),
            consequence_class=str(value["consequence_class"]),
            reversible=value["reversible"],
            dependencies=tuple(str(item) for item in value.get("dependencies", [])),
            preconditions=tuple(
                Condition.from_dict(item) for item in value.get("preconditions", [])
            ),
            expected_observations=tuple(
                ExpectedObservation.from_dict(item)
                for item in value.get("expected_observations", [])
            ),
            expires_at=str(value["expires_at"]),
            rationale=str(value["rationale"]),
            cancellation_conditions=tuple(
                Condition.from_dict(item)
                for item in value.get("cancellation_conditions", [])
            ),
            resource_cost=float(value.get("resource_cost", 0.0)),
            requires_explicit_approval=value.get("requires_explicit_approval", False),
        )


@dataclass(frozen=True, slots=True)
class StructuredPlan:
    plan_id: str
    version: int
    goal_id: str
    situation_id: str
    situation_type: str
    created_at: str
    valid_until: str
    rationale: str
    context_snapshot: Mapping[str, Any]
    evidence_ids: tuple[str, ...]
    steps: tuple[PlanStep, ...]
    resource_budget: float
    status: PlanStatus = PlanStatus.ACTIVE
    removed_step_ids: tuple[str, ...] = ()
    edited_by_marc: bool = False
    cancellation_reason: str = ""

    def __post_init__(self) -> None:
        if not self.plan_id or self.version < 1 or not self.goal_id:
            raise ValueError("plan identity, version, and goal are required")
        if not self.situation_id or not self.situation_type:
            raise ValueError("plan situation identity is required")
        if not self.rationale:
            raise ValueError("plan rationale is required")
        _require_finite_number(self.resource_budget, "plan resource budget")
        _require_bool(self.edited_by_marc, "plan edit authority")
        if self.resource_budget < 0:
            raise ValueError("plan resource budget must not be negative")
        created = _parse_timestamp(self.created_at)
        if _parse_timestamp(self.valid_until) <= created:
            raise ValueError("plan expiration must be after creation")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step IDs must be unique")
        if set(self.removed_step_ids) & set(step_ids):
            raise ValueError("removed plan steps cannot be present in a version")

    @property
    def context_fingerprint(self) -> str:
        return hashlib.sha256(_json(dict(self.context_snapshot)).encode()).hexdigest()

    def step(self, step_id: str) -> PlanStep:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise KeyError(step_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "version": self.version,
            "goal_id": self.goal_id,
            "situation_id": self.situation_id,
            "situation_type": self.situation_type,
            "created_at": self.created_at,
            "valid_until": self.valid_until,
            "rationale": self.rationale,
            "context_snapshot": dict(self.context_snapshot),
            "context_fingerprint": self.context_fingerprint,
            "evidence_ids": list(self.evidence_ids),
            "steps": [step.to_dict() for step in self.steps],
            "resource_budget": self.resource_budget,
            "status": self.status.value,
            "removed_step_ids": list(self.removed_step_ids),
            "edited_by_marc": self.edited_by_marc,
            "cancellation_reason": self.cancellation_reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StructuredPlan":
        allowed = {
            "plan_id",
            "version",
            "goal_id",
            "situation_id",
            "situation_type",
            "created_at",
            "valid_until",
            "rationale",
            "context_snapshot",
            "context_fingerprint",
            "evidence_ids",
            "steps",
            "resource_budget",
            "status",
            "removed_step_ids",
            "edited_by_marc",
            "cancellation_reason",
        }
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"unknown plan fields: {sorted(extra)}")
        required = {
            "plan_id",
            "version",
            "goal_id",
            "situation_id",
            "situation_type",
            "created_at",
            "valid_until",
            "rationale",
            "context_snapshot",
            "resource_budget",
        }
        missing = required - set(value)
        if missing:
            raise ValueError(f"missing plan fields: {sorted(missing)}")
        snapshot = dict(value["context_snapshot"])
        plan = cls(
            plan_id=str(value["plan_id"]),
            version=int(value["version"]),
            goal_id=str(value["goal_id"]),
            situation_id=str(value["situation_id"]),
            situation_type=str(value["situation_type"]),
            created_at=str(value["created_at"]),
            valid_until=str(value["valid_until"]),
            rationale=str(value["rationale"]),
            context_snapshot=snapshot,
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", [])),
            steps=tuple(PlanStep.from_dict(item) for item in value.get("steps", [])),
            resource_budget=float(value["resource_budget"]),
            status=PlanStatus(str(value.get("status", PlanStatus.ACTIVE.value))),
            removed_step_ids=tuple(
                str(item) for item in value.get("removed_step_ids", [])
            ),
            edited_by_marc=value.get("edited_by_marc", False),
            cancellation_reason=str(value.get("cancellation_reason", "")),
        )
        fingerprint = value.get("context_fingerprint")
        if fingerprint is not None and str(fingerprint) != plan.context_fingerprint:
            raise ValueError("plan context fingerprint does not match its snapshot")
        return plan


@dataclass(frozen=True, slots=True)
class ContextualGrant:
    """A narrow, persisted grant whose scope is never inferred from prose."""

    grant_id: str
    action_type: str
    target: str
    workspace: str
    location: str
    household_mode: str
    situation_id: str
    situation_type: str
    calendar_event_id: str
    calendar_event_start: str
    plan_id: str
    plan_version: int
    step_id: str
    time_window_start: str
    time_window_end: str
    expires_at: str
    min_confidence: float
    max_source_age_seconds: int
    required_observation_keys: tuple[str, ...]
    frequency_limit: int
    frequency_window_seconds: int
    resource_budget: float
    resource_cost: float
    presence: tuple[str, ...]
    reversible: bool
    consequence_class: str
    edited_by_marc: bool
    created_at: str
    revoked_at: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.grant_id,
            self.action_type,
            self.target,
            self.workspace,
            self.location,
            self.household_mode,
            self.situation_id,
            self.situation_type,
            self.calendar_event_id,
            self.calendar_event_start,
            self.plan_id,
            self.step_id,
            self.time_window_start,
            self.time_window_end,
            self.expires_at,
            self.consequence_class,
            self.created_at,
        )
        if any(not item for item in required) or self.plan_version < 1:
            raise ValueError("contextual grants require a complete exact scope")
        if any(item == "*" for item in required):
            raise ValueError("contextual grants do not support wildcard scope")
        _require_bool(self.reversible, "grant reversibility")
        _require_bool(self.edited_by_marc, "grant edit authority")
        _require_finite_number(self.min_confidence, "grant confidence")
        _require_finite_number(self.resource_budget, "grant resource budget")
        _require_finite_number(self.resource_cost, "grant resource cost")
        if self.min_confidence < 0 or self.min_confidence > 1:
            raise ValueError("grant confidence must be between 0 and 1")
        if self.max_source_age_seconds < 0:
            raise ValueError("grant source freshness must not be negative")
        if self.frequency_limit < 1 or self.frequency_window_seconds < 1:
            raise ValueError("grant frequency limits must be positive")
        if self.resource_budget < 0 or self.resource_cost < 0:
            raise ValueError("grant resource budgets must not be negative")
        if self.resource_cost > self.resource_budget:
            raise ValueError("grant resource cost exceeds its budget")
        start = _parse_timestamp(self.time_window_start)
        end = _parse_timestamp(self.time_window_end)
        expires = _parse_timestamp(self.expires_at)
        created = _parse_timestamp(self.created_at)
        _parse_timestamp(self.calendar_event_start)
        if end <= start or expires <= created or expires < end:
            raise ValueError("grant time window and expiration are invalid")
        if self.revoked_at is not None:
            _parse_timestamp(self.revoked_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "action_type": self.action_type,
            "target": self.target,
            "workspace": self.workspace,
            "location": self.location,
            "household_mode": self.household_mode,
            "situation_id": self.situation_id,
            "situation_type": self.situation_type,
            "calendar_event_id": self.calendar_event_id,
            "calendar_event_start": self.calendar_event_start,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "step_id": self.step_id,
            "time_window_start": self.time_window_start,
            "time_window_end": self.time_window_end,
            "expires_at": self.expires_at,
            "min_confidence": self.min_confidence,
            "max_source_age_seconds": self.max_source_age_seconds,
            "required_observation_keys": list(self.required_observation_keys),
            "frequency_limit": self.frequency_limit,
            "frequency_window_seconds": self.frequency_window_seconds,
            "resource_budget": self.resource_budget,
            "resource_cost": self.resource_cost,
            "presence": list(self.presence),
            "reversible": self.reversible,
            "consequence_class": self.consequence_class,
            "edited_by_marc": self.edited_by_marc,
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextualGrant":
        allowed = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"unknown grant fields: {sorted(extra)}")
        return cls(
            grant_id=str(value["grant_id"]),
            action_type=str(value["action_type"]),
            target=str(value["target"]),
            workspace=str(value["workspace"]),
            location=str(value["location"]),
            household_mode=str(value["household_mode"]),
            situation_id=str(value["situation_id"]),
            situation_type=str(value["situation_type"]),
            calendar_event_id=str(value["calendar_event_id"]),
            calendar_event_start=str(value["calendar_event_start"]),
            plan_id=str(value["plan_id"]),
            plan_version=int(value["plan_version"]),
            step_id=str(value["step_id"]),
            time_window_start=str(value["time_window_start"]),
            time_window_end=str(value["time_window_end"]),
            expires_at=str(value["expires_at"]),
            min_confidence=float(value["min_confidence"]),
            max_source_age_seconds=int(value["max_source_age_seconds"]),
            required_observation_keys=tuple(
                str(item) for item in value.get("required_observation_keys", [])
            ),
            frequency_limit=int(value["frequency_limit"]),
            frequency_window_seconds=int(value["frequency_window_seconds"]),
            resource_budget=float(value["resource_budget"]),
            resource_cost=float(value["resource_cost"]),
            presence=tuple(str(item) for item in value.get("presence", [])),
            reversible=value["reversible"],
            consequence_class=str(value["consequence_class"]),
            edited_by_marc=value["edited_by_marc"],
            created_at=str(value["created_at"]),
            revoked_at=value.get("revoked_at"),
        )


@dataclass(frozen=True, slots=True)
class ValidationReport:
    valid: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    plan_id: str
    plan_version: int
    step_id: str
    reason: str
    authorization: Authorization | None = None
    grant_id: str | None = None
    autonomy_level: AutonomyLevel = AutonomyLevel.SHADOW
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "step_id": self.step_id,
            "reason": self.reason,
            "authorization": self.authorization.to_dict()
            if self.authorization
            else None,
            "grant_id": self.grant_id,
            "autonomy_level": self.autonomy_level.value,
            "evidence": list(self.evidence),
        }


class PlanValidationError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__("invalid structured plan: " + "; ".join(report.reasons))


__all__ = [
    "AuthorizationDecision",
    "AutonomyLevel",
    "Condition",
    "ContextualGrant",
    "ExpectedObservation",
    "ObservationSnapshot",
    "PlanStatus",
    "PlanStep",
    "PlanValidationError",
    "PlanningContext",
    "StructuredPlan",
    "ValidationReport",
]
