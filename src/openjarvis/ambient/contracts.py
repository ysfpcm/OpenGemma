"""Typed, JSON-round-trippable Phase 11 ambient contracts.

These records deliberately describe intent, continuity, consent, and evidence;
none of them is an execution capability.  External channel adapters are a
separate boundary and are disabled by the deterministic acceptance lane.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, TypeVar

SCHEMA_VERSION = 1
T = TypeVar("T", bound="AmbientContract")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AmbientContractError(ValueError):
    """Raised when an ambient contract is malformed or unsafe."""


class PresenceState(str, Enum):
    AT_DESK = "at_desk"
    AWAY_FROM_PC = "away_from_pc"
    RETURNED = "returned"
    UNKNOWN = "unknown"


class InterruptionKind(str, Enum):
    BARGE_IN = "barge_in"
    CANCEL = "cancel"
    EMERGENCY_STOP = "emergency_stop"
    REVOCATION = "revocation"
    BUDGET_EXCEEDED = "budget_exceeded"
    REMOTE_CHANNEL_FAILURE = "remote_channel_failure"


class InterruptionStatus(str, Enum):
    REQUESTED = "requested"
    APPLIED = "applied"
    DECLINED = "declined"
    RECOVERED_UNKNOWN = "recovered_unknown"


class UpdateKind(str, Enum):
    MILESTONE = "milestone"
    DECISION = "decision"
    BLOCKER = "blocker"
    COMPLETION = "completion"
    ERROR = "error"
    HEARTBEAT = "heartbeat"


class UpdateDeliveryStatus(str, Enum):
    READY = "ready"
    DELIVERED_LOCAL = "delivered_local"
    SUPPRESSED_UNCHANGED = "suppressed_unchanged"
    BLOCKED_CONSENT = "blocked_consent"
    BLOCKED_SENSITIVITY = "blocked_sensitivity"
    FAILED_CHANNEL = "failed_channel"
    CANCELED = "canceled"


class SteeringStatus(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    EXECUTED_LOCAL = "executed_local"
    DECLINED = "declined"
    STALE = "stale"
    CANCELED = "canceled"
    REVOKED = "revoked"
    BUDGET_EXCEEDED = "budget_exceeded"
    EMERGENCY_STOPPED = "emergency_stopped"
    FAILED = "failed"


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


@dataclass
class AmbientContract:
    """Common causal envelope for every Phase 11 durable record."""

    contract_type: ClassVar[str] = "ambient_contract"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)
    provenance: dict[str, Any] = field(default_factory=dict)
    causal_parents: list[str] = field(default_factory=list)
    sensitivity_labels: list[str] = field(default_factory=list)
    confidence: float | None = None
    _enum_fields: ClassVar[dict[str, type[Enum]]] = {}

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise AmbientContractError(
                f"unsupported {self.contract_type} schema {self.schema_version}"
            )
        if not self.id:
            raise AmbientContractError("contract id is required")
        try:
            datetime.fromisoformat(self.created_at)
        except (TypeError, ValueError) as exc:
            raise AmbientContractError("created_at must be ISO-8601") from exc
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise AmbientContractError("confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return _encode({"contract_type": self.contract_type, **asdict(self)})

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls: type[T], value: dict[str, Any]) -> T:
        raw = dict(value)
        discriminator = raw.pop("contract_type", cls.contract_type)
        target = AMBIENT_CONTRACT_TYPES.get(discriminator)
        if target is None or (cls is not AmbientContract and target is not cls):
            raise AmbientContractError(
                f"unknown or incompatible contract {discriminator!r}"
            )
        allowed = {item.name for item in fields(target)}
        extra = set(raw) - allowed
        if extra:
            raise AmbientContractError(
                f"unexpected fields for {discriminator}: {sorted(extra)}"
            )
        for name, enum_type in target._enum_fields.items():
            if name in raw and not isinstance(raw[name], enum_type):
                raw[name] = enum_type(raw[name])
        return target(**raw)  # type: ignore[return-value]

    @classmethod
    def from_json(cls: type[T], value: str) -> T:
        raw = json.loads(value)
        if not isinstance(raw, dict):
            raise AmbientContractError("contract JSON root must be an object")
        return cls.from_dict(raw)


@dataclass
class Interruption(AmbientContract):
    contract_type: ClassVar[str] = "interruption"
    interruption_id: str = ""
    mission_id: str = ""
    turn_id: str = ""
    kind: InterruptionKind = InterruptionKind.BARGE_IN
    priority: int = 0
    status: InterruptionStatus = InterruptionStatus.REQUESTED
    requested_by: str = "Marc"
    reason: str = ""
    applies_to: list[str] = field(default_factory=list)
    cancellation_state: str = "not_canceled"
    _enum_fields: ClassVar[dict[str, type[Enum]]] = {
        "kind": InterruptionKind,
        "status": InterruptionStatus,
    }

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.interruption_id or not self.mission_id or not self.turn_id:
            raise AmbientContractError("interruption identity is required")
        if self.priority < 0 or not self.reason:
            raise AmbientContractError("interruption priority and reason are required")


@dataclass
class AmbientPresence(AmbientContract):
    contract_type: ClassVar[str] = "ambient_presence"
    presence_id: str = ""
    mission_id: str = ""
    state: PresenceState = PresenceState.UNKNOWN
    source: str = "deterministic-fixture"
    active_indicator: bool = True
    uncertainty: list[str] = field(default_factory=list)
    observed_at: str = field(default_factory=utc_now)
    expires_at: str | None = None
    _enum_fields: ClassVar[dict[str, type[Enum]]] = {"state": PresenceState}

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.presence_id or not self.mission_id:
            raise AmbientContractError("presence_id and mission_id are required")
        if not self.active_indicator:
            raise AmbientContractError(
                "presence or visual context requires an active indicator"
            )


@dataclass
class MissionSummary(AmbientContract):
    contract_type: ClassVar[str] = "mission_summary"
    mission_id: str = ""
    thread_id: str = ""
    turn_id: str = ""
    workspace: str = ""
    goal: str = ""
    state: str = "active"
    latest_summary: str = ""
    blockers: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    source_evidence: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    budget: dict[str, int | float] = field(default_factory=dict)
    used_budget: dict[str, int | float] = field(default_factory=dict)
    last_update_seq: int = 0
    stale: bool = False
    canceled: bool = False
    authority_scope: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            not self.mission_id
            or not self.thread_id
            or not self.turn_id
            or not self.workspace
        ):
            raise AmbientContractError(
                "mission identity, turn, and workspace are required"
            )
        if not self.goal:
            raise AmbientContractError("mission goal is required")
        if self.last_update_seq < 0:
            raise AmbientContractError("last_update_seq cannot be negative")


@dataclass
class MissionUpdate(AmbientContract):
    contract_type: ClassVar[str] = "mission_update"
    update_id: str = ""
    mission_id: str = ""
    turn_id: str = ""
    sequence: int = 0
    kind: UpdateKind = UpdateKind.MILESTONE
    summary: str = ""
    materiality: float = 0.0
    state: str = "active"
    changed_fields: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    force_delivery: bool = False
    _enum_fields: ClassVar[dict[str, type[Enum]]] = {"kind": UpdateKind}

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.update_id or not self.mission_id or not self.turn_id:
            raise AmbientContractError("update identity is required")
        if self.sequence < 0 or not 0 <= self.materiality <= 1:
            raise AmbientContractError("invalid update sequence or materiality")
        if not self.summary:
            raise AmbientContractError("update summary is required")


@dataclass
class NotificationPolicy(AmbientContract):
    contract_type: ClassVar[str] = "notification_policy"
    policy_id: str = ""
    channel: str = "local-desktop"
    minimum_materiality: float = 0.5
    suppress_unchanged: bool = True
    allowed_sensitivity: list[str] = field(
        default_factory=lambda: ["public", "workspace"]
    )
    require_consent_id: str | None = None
    live_effects_enabled: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.policy_id or not self.channel:
            raise AmbientContractError("policy_id and channel are required")
        if not 0 <= self.minimum_materiality <= 1:
            raise AmbientContractError("minimum_materiality must be between 0 and 1")
        if self.live_effects_enabled:
            raise AmbientContractError(
                "Phase 11 deterministic lane cannot enable live effects"
            )


@dataclass
class UpdateDelivery(AmbientContract):
    contract_type: ClassVar[str] = "update_delivery"
    delivery_id: str = ""
    update_id: str = ""
    mission_id: str = ""
    channel: str = "local-desktop"
    status: UpdateDeliveryStatus = UpdateDeliveryStatus.READY
    safe_summary: str = ""
    suppressed_reason: str = ""
    delivered_at: str | None = None
    consent_id: str | None = None
    redaction_id: str | None = None
    _enum_fields: ClassVar[dict[str, type[Enum]]] = {"status": UpdateDeliveryStatus}

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.delivery_id or not self.update_id or not self.mission_id:
            raise AmbientContractError("delivery identity is required")


@dataclass
class ChannelConsent(AmbientContract):
    contract_type: ClassVar[str] = "channel_consent"
    consent_id: str = ""
    channel: str = "local-desktop"
    mission_id: str = ""
    operations: list[str] = field(default_factory=list)
    allowed_sensitivity: list[str] = field(
        default_factory=lambda: ["public", "workspace"]
    )
    active_indicator_required: bool = True
    active: bool = True
    expires_at: str | None = None
    revoked_at: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.consent_id or not self.channel or not self.mission_id:
            raise AmbientContractError("consent identity is required")
        if not self.active_indicator_required:
            raise AmbientContractError(
                "channel consent must require an active indicator"
            )


@dataclass
class RedactionEvidence(AmbientContract):
    contract_type: ClassVar[str] = "redaction_evidence"
    redaction_id: str = ""
    source_id: str = ""
    channel: str = ""
    fields_redacted: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    safe_digest: str = ""
    secret_values_persisted: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.redaction_id or not self.source_id or not self.channel:
            raise AmbientContractError("redaction identity is required")
        if self.secret_values_persisted:
            raise AmbientContractError(
                "secret values cannot be persisted in redaction evidence"
            )


@dataclass
class ContinuityAcknowledgment(AmbientContract):
    contract_type: ClassVar[str] = "continuity_acknowledgment"
    acknowledgment_id: str = ""
    mission_id: str = ""
    channel: str = "local-desktop"
    last_update_seq: int = 0
    last_update_id: str | None = None
    presence_state: PresenceState = PresenceState.UNKNOWN
    speech_state: str = "idle"
    continuity_state: str = "continuous"
    acknowledged_at: str = field(default_factory=utc_now)
    _enum_fields: ClassVar[dict[str, type[Enum]]] = {"presence_state": PresenceState}

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.acknowledgment_id or not self.mission_id:
            raise AmbientContractError("acknowledgment identity is required")


@dataclass
class MissionPortfolioItem(AmbientContract):
    contract_type: ClassVar[str] = "mission_portfolio_item"
    item_id: str = ""
    goal: str = ""
    source_evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    uncertainty: list[str] = field(default_factory=list)
    authority_scope: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, int | float] = field(default_factory=dict)
    cancellation_state: str = "cancelable"
    available_time_minutes: int = 0
    attention_cost: float = 0.0
    financial_cost: float = 0.0
    urgency: float = 0.0
    value: float = 0.0
    ranked_score: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.item_id or not self.goal:
            raise AmbientContractError("portfolio identity and goal are required")
        for value in (
            self.confidence,
            self.attention_cost,
            self.financial_cost,
            self.urgency,
            self.value,
        ):
            if not 0 <= value <= 1:
                raise AmbientContractError("portfolio scores must be between 0 and 1")
        if self.available_time_minutes < 0:
            raise AmbientContractError("available_time_minutes cannot be negative")


@dataclass
class AuthorityGrantScope(AmbientContract):
    contract_type: ClassVar[str] = "authority_grant_scope"
    grant_id: str = ""
    mission_id: str = ""
    turn_id: str = ""
    workspace: str = ""
    capability: str = ""
    expires_at: str | None = None
    revoked: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not all(
            (
                self.grant_id,
                self.mission_id,
                self.turn_id,
                self.workspace,
                self.capability,
            )
        ):
            raise AmbientContractError("authority scope must be exact and complete")


@dataclass
class RemoteQuery(AmbientContract):
    contract_type: ClassVar[str] = "remote_query"
    query_id: str = ""
    mission_id: str = ""
    channel: str = "phone"
    question: str = ""
    consent_id: str = ""
    status: str = "requested"
    answer: str = ""
    answer_as_of_update_seq: int = 0
    stale: bool = False
    redaction_id: str | None = None
    failure_reason: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            not self.query_id
            or not self.mission_id
            or not self.question
            or not self.consent_id
        ):
            raise AmbientContractError("remote query identity and consent are required")


@dataclass
class RemoteSteering(AmbientContract):
    contract_type: ClassVar[str] = "remote_steering"
    steering_id: str = ""
    mission_id: str = ""
    turn_id: str = ""
    workspace: str = ""
    channel: str = "phone"
    consent_id: str = ""
    authority_grant_id: str = ""
    operation: str = "fork_safer_stop_original_if_tests_fail"
    status: SteeringStatus = SteeringStatus.REQUESTED
    requested_condition: str = "tests_fail"
    fork_id: str | None = None
    original_stop_requested: bool = False
    live_effect: bool = False
    reason: str = ""
    _enum_fields: ClassVar[dict[str, type[Enum]]] = {"status": SteeringStatus}

    def __post_init__(self) -> None:
        super().__post_init__()
        if not all(
            (
                self.steering_id,
                self.mission_id,
                self.turn_id,
                self.workspace,
                self.consent_id,
                self.authority_grant_id,
            )
        ):
            raise AmbientContractError("remote steering exact scope is required")
        if self.live_effect:
            raise AmbientContractError(
                "deterministic Phase 11 steering cannot claim live effect"
            )


AMBIENT_CONTRACT_TYPES: dict[str, type[AmbientContract]] = {
    item.contract_type: item
    for item in (
        Interruption,
        AmbientPresence,
        MissionSummary,
        MissionUpdate,
        NotificationPolicy,
        UpdateDelivery,
        ChannelConsent,
        RedactionEvidence,
        ContinuityAcknowledgment,
        MissionPortfolioItem,
        AuthorityGrantScope,
        RemoteQuery,
        RemoteSteering,
    )
}


def contract_from_dict(value: dict[str, Any]) -> AmbientContract:
    return AmbientContract.from_dict(value)


__all__ = [
    "AMBIENT_CONTRACT_TYPES",
    "AmbientContract",
    "AmbientContractError",
    "AmbientPresence",
    "AuthorityGrantScope",
    "ChannelConsent",
    "ContinuityAcknowledgment",
    "Interruption",
    "InterruptionKind",
    "InterruptionStatus",
    "MissionPortfolioItem",
    "MissionSummary",
    "MissionUpdate",
    "NotificationPolicy",
    "PresenceState",
    "RedactionEvidence",
    "RemoteQuery",
    "RemoteSteering",
    "SteeringStatus",
    "UpdateDelivery",
    "UpdateDeliveryStatus",
    "UpdateKind",
    "contract_from_dict",
    "utc_now",
]
