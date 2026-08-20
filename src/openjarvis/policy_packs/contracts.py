"""Typed, local-first contracts for Phase 12 policy packs and embodiment.

The contracts in this module are deliberately independent from policy logic and
from any physical adapter.  A pack can describe bounded capabilities, but an
installation grant and the existing Guardian action lifecycle remain the only
paths to an action proposal or effect.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Mapping, Optional

PHASE12_SCHEMA_VERSION = "1.0"
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$"
)
_SENSITIVE_KEYS = {
    "secret",
    "secrets",
    "token",
    "password",
    "credential",
    "credentials",
    "private_key",
    "chain_of_thought",
    "reasoning_trace",
    "raw_diff",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_safe(value: Any, path: str = "metadata") -> None:
    """Reject metadata that could accidentally carry secrets or private dumps."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS or any(
                marker in normalized
                for marker in ("password", "credential", "secret", "token")
            ):
                raise ValueError(f"{path}.{key} is not allowed in a pack contract")
            _validate_safe(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _validate_safe(item, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if "chain-of-thought" in lowered or "chain_of_thought" in lowered:
            raise ValueError(f"{path} cannot contain hidden reasoning claims")


class PackInstallState(str, Enum):
    INSTALLED = "installed"
    UNINSTALLED = "uninstalled"
    REVOKED = "revoked"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class PackOperation(str, Enum):
    INSTALL = "install"
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    UNINSTALL = "uninstall"
    REINSTALL = "reinstall"
    ROLLBACK = "rollback"


class PolicyOutcome(str, Enum):
    PROPOSED = "proposed"
    NO_ACTION = "no_action"
    BLOCKED = "blocked"
    REPLAYED = "replayed"


class CommunicationStatus(str, Enum):
    CONNECTED = "connected"
    LIMITED = "limited"
    LOST = "lost"


class ControllerStatus(str, Enum):
    ACCEPTED = "accepted"
    REFUSED = "refused"
    STOPPED = "stopped"
    DUPLICATE = "duplicate"
    TIMED_OUT = "timed_out"


class FaultKind(str, Enum):
    UNSAFE_MOTION = "unsafe_motion"
    STALE_PERCEPTION = "stale_perception"
    CONTRADICTORY_PERCEPTION = "contradictory_perception"
    DUPLICATE_COMMAND = "duplicate_command"
    COMMUNICATION_LOSS = "communication_loss"
    CONTROLLER_TIMEOUT = "controller_timeout"
    REVOKED_AUTHORITY = "revoked_authority"
    EMERGENCY_STOP = "emergency_stop"
    CANCELED = "canceled"


class ContractMixin:
    schema_version: ClassVar[str] = PHASE12_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    def to_json(self) -> str:
        return _canonical(self.to_dict())


@dataclass(frozen=True)
class DependencySpec(ContractMixin):
    pack_id: str
    version_range: str = "*"
    optional: bool = False


@dataclass(frozen=True)
class MigrationSpec(ContractMixin):
    migration_id: str
    from_schema: str
    to_schema: str
    forward_digest: str
    rollback_digest: str
    reversible: bool = True

    def __post_init__(self) -> None:
        if not self.migration_id or not self.forward_digest or not self.rollback_digest:
            raise ValueError(
                "migration identity and both rollback digests are required"
            )
        if not self.reversible:
            raise ValueError("Phase 12 migrations must be reversible")


@dataclass(frozen=True)
class ActionSpec(ContractMixin):
    action_type: str
    capability: str
    risk_class: str
    consequence_class: str
    input_schema: dict[str, Any]
    explanation_key: str
    allowed_targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.action_type or not self.capability:
            raise ValueError("typed actions require action_type and capability")
        if self.input_schema.get("type") != "object":
            raise ValueError("typed action input schema must be an object")
        _validate_safe(self.input_schema, "input_schema")


@dataclass(frozen=True)
class ReleaseGate(ContractMixin):
    gate_id: str
    description: str
    required: bool = True
    passed: bool = False


@dataclass(frozen=True)
class ReplayFixtureRef(ContractMixin):
    fixture_id: str
    scenario: str
    expected_outcome: str
    fault_cases: tuple[str, ...] = ()
    release_gate_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyPackManifest(ContractMixin):
    pack_id: str
    name: str
    version: str
    publisher: str
    signature: str
    integrity_digest: str
    dependencies: tuple[DependencySpec, ...] = ()
    compatibility: dict[str, str] = field(
        default_factory=lambda: {"ophanim": ">=12.0,<13.0"}
    )
    schema_version: str = PHASE12_SCHEMA_VERSION
    migrations: tuple[MigrationSpec, ...] = ()
    triggers: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    situation_detector: str = ""
    evidence_requirements: tuple[str, ...] = ()
    supported_actions: tuple[ActionSpec, ...] = ()
    autonomy_defaults: dict[str, str] = field(default_factory=dict)
    authority_limits: dict[str, Any] = field(default_factory=dict)
    ui_metadata: dict[str, Any] = field(default_factory=dict)
    replay_fixtures: tuple[ReplayFixtureRef, ...] = ()
    fault_cases: tuple[str, ...] = ()
    release_gates: tuple[ReleaseGate, ...] = ()
    lifecycle_behavior: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.pack_id or not self.name or not self.publisher:
            raise ValueError("pack_id, name, and publisher are required")
        if not _SEMVER.match(self.version):
            raise ValueError(f"invalid pack version: {self.version}")
        if self.schema_version != PHASE12_SCHEMA_VERSION:
            raise ValueError(f"unsupported pack schema: {self.schema_version}")
        _validate_safe(self.compatibility, "compatibility")
        _validate_safe(self.autonomy_defaults, "autonomy_defaults")
        _validate_safe(self.authority_limits, "authority_limits")
        _validate_safe(self.ui_metadata, "ui_metadata")
        _validate_safe(self.lifecycle_behavior, "lifecycle_behavior")

    def unsigned_payload(self) -> dict[str, Any]:
        body = self.to_dict()
        body.pop("signature", None)
        body.pop("integrity_digest", None)
        return body

    def calculated_integrity_digest(self) -> str:
        return hashlib.sha256(
            _canonical(self.unsigned_payload()).encode("utf-8")
        ).hexdigest()

    def signed(self, key: str) -> "PolicyPackManifest":
        digest = self.calculated_integrity_digest()
        signature = hmac.new(
            key.encode("utf-8"), digest.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return PolicyPackManifest.from_dict(
            {
                **self.to_dict(),
                "signature": f"hmac-sha256:{signature}",
                "integrity_digest": digest,
            }
        )

    def verify(self, trusted_publishers: Mapping[str, str]) -> tuple[bool, str]:
        if self.publisher not in trusted_publishers:
            return False, "publisher is not trusted"
        if self.integrity_digest != self.calculated_integrity_digest():
            return False, "integrity digest mismatch"
        if not self.signature.startswith("hmac-sha256:"):
            return False, "unsupported or missing signature"
        expected = hmac.new(
            trusted_publishers[self.publisher].encode("utf-8"),
            self.integrity_digest.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(
            self.signature.removeprefix("hmac-sha256:"), expected
        ):
            return False, "signature verification failed"
        return True, "verified"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PolicyPackManifest":
        data = dict(raw)
        data["dependencies"] = tuple(
            DependencySpec(**item) for item in data.get("dependencies", ())
        )
        data["migrations"] = tuple(
            MigrationSpec(**item) for item in data.get("migrations", ())
        )
        data["supported_actions"] = tuple(
            ActionSpec(**item) for item in data.get("supported_actions", ())
        )
        data["replay_fixtures"] = tuple(
            ReplayFixtureRef(**item) for item in data.get("replay_fixtures", ())
        )
        data["release_gates"] = tuple(
            ReleaseGate(**item) for item in data.get("release_gates", ())
        )
        for key in (
            "dependencies",
            "migrations",
            "supported_actions",
            "replay_fixtures",
            "release_gates",
        ):
            data[key] = tuple(data[key])
        return cls(**data)

    @classmethod
    def from_json(cls, raw: str) -> "PolicyPackManifest":
        return cls.from_dict(json.loads(raw))


@dataclass(frozen=True)
class InstallationGrant(ContractMixin):
    grant_id: str
    pack_id: str
    installation_id: str
    allowed_action_types: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()
    target_scope: dict[str, Any] = field(default_factory=dict)
    max_risk_class: str = "low"
    max_consequence_class: str = "low"
    installed_by: str = "Marc"
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.grant_id or not self.pack_id or not self.installation_id:
            raise ValueError("installation grants require stable identity")
        _validate_safe(self.target_scope, "target_scope")
        _validate_safe(self.provenance, "provenance")

    @property
    def revoked(self) -> bool:
        if self.revoked_at:
            return True
        return bool(
            self.expires_at
            and _parse_time(self.expires_at) <= datetime.now(timezone.utc)
        )

    def allows(self, action: ActionSpec, parameters: Mapping[str, Any]) -> bool:
        if self.revoked:
            return False
        if (
            self.allowed_action_types
            and action.action_type not in self.allowed_action_types
        ):
            return False
        if (
            self.allowed_capabilities
            and action.capability not in self.allowed_capabilities
        ):
            return False
        if _risk_rank(action.risk_class) > _risk_rank(self.max_risk_class):
            return False
        if _risk_rank(action.consequence_class) > _risk_rank(
            self.max_consequence_class
        ):
            return False
        for key, expected in self.target_scope.items():
            if parameters.get(key) != expected:
                return False
        return True


@dataclass(frozen=True)
class PackInstallation(ContractMixin):
    installation_id: str
    manifest: PolicyPackManifest
    state: PackInstallState
    authority_scope: InstallationGrant
    installed_at: str
    updated_at: str
    previous_version: Optional[str] = None
    migration_state: str = "none"
    rollback_state: str = "available"
    production_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["manifest"] = self.manifest.to_dict()
        data["authority_scope"] = self.authority_scope.to_dict()
        data["state"] = self.state.value
        return data


@dataclass(frozen=True)
class PolicyEvidence(ContractMixin):
    evidence_id: str
    source_id: str
    observed_at: str
    confidence: float = 1.0
    freshness_seconds: int = 300
    contradictory: bool = False
    sensitivity: str = "operational"
    provenance: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.source_id:
            raise ValueError("policy evidence requires stable identity and source")
        try:
            confidence_is_valid = (
                math.isfinite(self.confidence) and 0.0 <= self.confidence <= 1.0
            )
        except TypeError as exc:
            raise ValueError(
                "policy evidence confidence must be between 0 and 1"
            ) from exc
        if not confidence_is_valid:
            raise ValueError("policy evidence confidence must be between 0 and 1")
        if self.freshness_seconds < 0:
            raise ValueError("policy evidence freshness cannot be negative")
        try:
            observed_at = _parse_time(self.observed_at)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "policy evidence observed_at must be an ISO timestamp"
            ) from exc
        if observed_at.tzinfo is None:
            raise ValueError("policy evidence observed_at must include a timezone")
        _validate_safe(self.provenance, "evidence.provenance")

    def is_fresh(self, now: Optional[str] = None) -> bool:
        try:
            reference = _parse_time(now or utc_now())
            age_seconds = (reference - _parse_time(self.observed_at)).total_seconds()
        except (TypeError, ValueError):
            return False
        return 0 <= age_seconds <= self.freshness_seconds


@dataclass(frozen=True)
class PolicyEvent(ContractMixin):
    event_id: str
    scenario: str
    observed_at: str
    sources: tuple[str, ...]
    evidence: tuple[PolicyEvidence, ...]
    dedupe_key: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id or not self.dedupe_key:
            raise ValueError("policy events require event_id and dedupe_key")
        try:
            observed_at = _parse_time(self.observed_at)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "policy event observed_at must be an ISO timestamp"
            ) from exc
        if observed_at.tzinfo is None:
            raise ValueError("policy event observed_at must include a timezone")
        _validate_safe(self.metadata, "event.metadata")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PolicyEvent":
        data = dict(raw)
        data["evidence"] = tuple(
            PolicyEvidence(**item) for item in data.get("evidence", ())
        )
        data["sources"] = tuple(data.get("sources", ()))
        return cls(**data)


@dataclass(frozen=True)
class PolicyActionProposal(ContractMixin):
    proposal_id: str
    action_type: str
    parameters: dict[str, Any]
    risk_class: str
    consequence_class: str
    plan_id: str
    installation_id: str
    idempotency_key: str
    expected_effect: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CausalTimelineEntry(ContractMixin):
    stage: str
    record_id: str
    status: str
    explanation: str
    causal_parents: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyEvaluation(ContractMixin):
    evaluation_id: str
    event_id: str
    pack_id: str
    pack_version: str
    outcome: PolicyOutcome
    explanation: str
    reasons: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    proposals: tuple[PolicyActionProposal, ...] = ()
    timeline: tuple[CausalTimelineEntry, ...] = ()
    reversible: bool = True
    simulation_only: bool = True
    live_effects: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["outcome"] = self.outcome.value
        return data


@dataclass(frozen=True)
class DigitalMissionTemplate(ContractMixin):
    template_id: str
    name: str
    objective_pattern: str
    action_types: tuple[str, ...]
    writable_roots: tuple[str, ...] = ()
    network_allowed: bool = False
    production_activation: bool = False
    explanation: str = ""

    def __post_init__(self) -> None:
        if self.network_allowed or self.production_activation:
            raise ValueError(
                "Phase 12 digital embodiment templates are local and non-production"
            )


@dataclass(frozen=True)
class EmbodimentIntent(ContractMixin):
    intent_id: str
    pack_id: str
    installation_id: str
    action_type: str
    target: str
    parameters: dict[str, Any]
    source: str = "world-model"
    proposal_only: bool = True
    confidence: float = 1.0
    perception_id: str = ""
    idempotency_key: str = ""
    created_at: str = field(default_factory=utc_now)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.proposal_only:
            raise ValueError("VLA/world-model intents must remain proposals")
        if not self.idempotency_key:
            object.__setattr__(self, "idempotency_key", f"embodiment:{self.intent_id}")
        if any(
            "motor" in str(key).lower() or "torque" in str(key).lower()
            for key in self.parameters
        ):
            raise ValueError("direct motor or torque fields are not accepted")
        _validate_safe(self.provenance, "intent.provenance")

    @classmethod
    def from_json(cls, raw: str) -> "EmbodimentIntent":
        return cls(**json.loads(raw))


@dataclass(frozen=True)
class PerceptionSnapshot(ContractMixin):
    perception_id: str
    observed_at: str
    state: dict[str, Any]
    confidence: float = 1.0
    contradictory: bool = False
    source: str = "simulator"

    def __post_init__(self) -> None:
        _validate_safe(self.state, "perception.state")


@dataclass(frozen=True)
class CommunicationState(ContractMixin):
    connection_id: str
    status: CommunicationStatus
    observed_at: str = field(default_factory=utc_now)
    sequence: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class SimulatedActuatorCommand(ContractMixin):
    command_id: str
    intent_id: str
    actuator: str
    operation: str
    target: str
    value: float
    sequence: int
    idempotency_key: str
    controller_id: str
    simulation_only: bool = True

    def __post_init__(self) -> None:
        if not self.simulation_only:
            raise ValueError("Phase 12 actuator commands must be simulation-only")
        if self.value != self.value or abs(self.value) > 1.0:
            raise ValueError("simulated actuator value must be finite and bounded")


@dataclass(frozen=True)
class SafetyCheck(ContractMixin):
    name: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class SafetyEnvelope(ContractMixin):
    envelope_id: str
    allowed_actuators: tuple[str, ...] = ("virtual_arm", "virtual_gripper")
    allowed_operations: tuple[str, ...] = ("nudge", "open", "close", "hold")
    max_step: float = 0.25
    perception_max_age_seconds: int = 2
    controller_timeout_seconds: int = 2
    communication_required: bool = True
    emergency_stop_active: bool = False

    def check(
        self,
        command: SimulatedActuatorCommand,
        perception: PerceptionSnapshot,
        communication: CommunicationState,
        *,
        now: Optional[str] = None,
    ) -> tuple[bool, tuple[SafetyCheck, ...]]:
        checks = [
            SafetyCheck(
                "actuator-allow-list",
                command.actuator in self.allowed_actuators,
                "actuator is not in the local vocabulary",
            ),
            SafetyCheck(
                "operation-allow-list",
                command.operation in self.allowed_operations,
                "operation is not in the local vocabulary",
            ),
            SafetyCheck(
                "bounded-step",
                abs(command.value) <= self.max_step,
                "requested motion exceeds the envelope",
            ),
            SafetyCheck(
                "fresh-perception",
                _fresh(perception.observed_at, self.perception_max_age_seconds, now),
                "perception is stale",
            ),
            SafetyCheck(
                "non-contradictory-perception",
                not perception.contradictory,
                "perception is contradictory",
            ),
            SafetyCheck(
                "communication",
                (not self.communication_required)
                or communication.status is CommunicationStatus.CONNECTED,
                "communication is not connected",
            ),
            SafetyCheck(
                "emergency-stop",
                not self.emergency_stop_active,
                "independent emergency stop is active",
            ),
        ]
        return all(item.passed for item in checks), tuple(checks)


@dataclass(frozen=True)
class ControllerDecision(ContractMixin):
    decision_id: str
    intent_id: str
    command_id: str
    status: ControllerStatus
    reason: str
    safety_checks: tuple[SafetyCheck, ...] = ()
    action_id: Optional[str] = None
    authorization_id: Optional[str] = None
    causal_parents: tuple[str, ...] = ()
    live_effects: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class SimulatedVerification(ContractMixin):
    verification_id: str
    action_id: str
    command_id: str
    expected_state: dict[str, Any]
    observed_state: dict[str, Any]
    succeeded: bool
    verifier: str = "independent-simulator-observer"
    simulation_only: bool = True


@dataclass(frozen=True)
class SafetyStop(ContractMixin):
    stop_id: str
    trigger: str
    reason: str
    command_id: Optional[str]
    actuator_state: str = "halted"
    emergency: bool = False
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class SimulationResult(ContractMixin):
    intent_id: str
    status: ControllerStatus
    decision: ControllerDecision
    verification: Optional[SimulatedVerification]
    action_id: Optional[str]
    state: dict[str, Any]
    live_effects: bool = False
    timeline: tuple[CausalTimelineEntry, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["status"] = self.status.value
        return data


def _fresh(observed_at: str, max_age_seconds: int, now: Optional[str]) -> bool:
    try:
        age = (
            datetime.now(timezone.utc) if now is None else _parse_time(now)
        ) - _parse_time(observed_at)
    except (TypeError, ValueError):
        return False
    return 0 <= age.total_seconds() <= max_age_seconds


def _risk_rank(value: str) -> int:
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(value.lower(), 99)


def new_id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


def version_satisfies(version: str, requirement: str) -> bool:
    """Small deterministic semver subset used by the offline pack manager."""

    if requirement in ("", "*"):
        return True
    try:
        current = _version_parts(version)
    except ValueError:
        return False
    for clause in requirement.split(","):
        clause = clause.strip()
        if not clause:
            continue
        operator = "="
        value = clause
        for candidate in (">=", "<=", ">", "<", "="):
            if clause.startswith(candidate):
                operator = candidate
                value = clause[len(candidate) :]
                break
        try:
            requested = _version_parts(value)
        except ValueError:
            return False
        if operator == ">=" and current < requested:
            return False
        if operator == "<=" and current > requested:
            return False
        if operator == ">" and current <= requested:
            return False
        if operator == "<" and current >= requested:
            return False
        if operator == "=" and current != requested:
            return False
    return True


def _version_parts(value: str) -> tuple[int, int, int]:
    numeric = value.split("+", 1)[0].split("-", 1)[0]
    parts = numeric.split(".")
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"invalid version: {value}")
    padded = [int(part) for part in parts]
    padded.extend([0] * (3 - len(padded)))
    return tuple(padded)  # type: ignore[return-value]


PackManifest = PolicyPackManifest
