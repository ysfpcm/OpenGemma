"""Typed records for the Phase 5 Living World Model.

The Phase 5 records deliberately keep claim semantics separate from source
provenance.  A claim may be a fact, hypothesis, or prediction, while its
information kind describes how Ophanim obtained it (observed, reported,
inferred, or predicted).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

PHASE5_SCHEMA_VERSION = 1


class EntityKind(str, Enum):
    PERSON = "person"
    PLACE = "place"
    DEVICE = "device"
    SERVICE = "service"
    PROJECT = "project"
    DOCUMENT = "document"
    EVENT = "event"


class InformationKind(str, Enum):
    OBSERVED = "observed"
    REPORTED = "reported"
    INFERRED = "inferred"
    PREDICTED = "predicted"


class ClaimClass(str, Enum):
    FACT = "fact"
    HYPOTHESIS = "hypothesis"
    PREDICTION = "prediction"


class BeliefStatus(str, Enum):
    CURRENT = "current"
    HISTORICAL = "historical"
    CONTRADICTED = "contradicted"
    TOMBSTONED = "tombstoned"


class EvidenceRole(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


class SourceStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DELETED = "deleted"


class MemoryLayer(str, Enum):
    SENSORY = "sensory_buffer"
    WORKING = "working_memory"
    EPISODIC = "episodic_memory"
    SEMANTIC = "semantic_memory"
    PROCEDURAL = "procedural_memory"
    RELATIONSHIP = "relationship_memory"
    SELF_MODEL = "protected_self_model"


def utc_iso(value: datetime | str | None = None) -> str:
    """Return a canonical UTC timestamp suitable for deterministic sorting."""

    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        canonical_json([prefix, *parts]).encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}_{digest}"


def enum_value(value: str | Enum) -> str:
    return value.value if isinstance(value, Enum) else str(value)


@dataclass(frozen=True, slots=True)
class Provenance:
    """The source chain that permits an answer to explain itself."""

    source_id: str
    source_type: str
    source_ref: str = ""
    observed_at: str = ""
    transformation: str = "direct"
    extractor: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "observed_at": self.observed_at,
            "transformation": self.transformation,
            "extractor": self.extractor,
        }


@dataclass(frozen=True, slots=True)
class WorldSource:
    source_id: str
    source_type: str
    label: str
    status: SourceStatus = SourceStatus.ACTIVE
    created_at: str = ""
    deleted_at: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "label": self.label,
            "status": enum_value(self.status),
            "created_at": self.created_at,
            "deleted_at": self.deleted_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Entity:
    entity_id: str
    kind: EntityKind
    canonical_name: str
    aliases: tuple[str, ...] = ()
    created_at: str = ""
    retired_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "kind": enum_value(self.kind),
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "created_at": self.created_at,
            "retired_at": self.retired_at,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    source_id: str
    entity_id: str
    predicate: str
    value: Any
    information_kind: InformationKind
    claim_class: ClaimClass
    confidence: float
    observed_at: str
    recorded_at: str
    provenance: Provenance
    evidence_text: str = ""
    sensitivity_labels: tuple[str, ...] = ()
    taint_labels: tuple[str, ...] = ()
    correction: bool = False
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_id": self.source_id,
            "entity_id": self.entity_id,
            "predicate": self.predicate,
            "value": self.value,
            "information_kind": enum_value(self.information_kind),
            "claim_class": enum_value(self.claim_class),
            "confidence": self.confidence,
            "observed_at": self.observed_at,
            "recorded_at": self.recorded_at,
            "provenance": self.provenance.to_dict(),
            "evidence_text": self.evidence_text,
            "sensitivity_labels": list(self.sensitivity_labels),
            "taint_labels": list(self.taint_labels),
            "correction": self.correction,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    role: EvidenceRole
    observation: Observation

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "role": enum_value(self.role),
            "observation": self.observation.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Belief:
    belief_id: str
    entity_id: str
    predicate: str
    value: Any
    claim_class: ClaimClass
    information_kind: InformationKind
    base_confidence: float
    confidence: float
    status: BeliefStatus
    created_at: str
    updated_at: str
    valid_from: str
    valid_until: str | None
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    uncertainty: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief_id": self.belief_id,
            "entity_id": self.entity_id,
            "predicate": self.predicate,
            "value": self.value,
            "claim_class": enum_value(self.claim_class),
            "information_kind": enum_value(self.information_kind),
            "base_confidence": self.base_confidence,
            "confidence": self.confidence,
            "status": enum_value(self.status),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "contradicting_evidence_ids": list(self.contradicting_evidence_ids),
            "provenance": [item.to_dict() for item in self.provenance],
            "uncertainty": list(self.uncertainty),
        }


@dataclass(frozen=True, slots=True)
class BeliefExplanation:
    entity_id: str
    predicate: str
    as_of: str
    current: Belief | None
    supports: tuple[Evidence, ...]
    contradicts: tuple[Evidence, ...]
    historical: tuple[Belief, ...]
    hypotheses: tuple[Belief, ...]
    predictions: tuple[Belief, ...]
    uncertainty: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "predicate": self.predicate,
            "as_of": self.as_of,
            "current": self.current.to_dict() if self.current else None,
            "supports": [item.to_dict() for item in self.supports],
            "contradicts": [item.to_dict() for item in self.contradicts],
            "historical": [item.to_dict() for item in self.historical],
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "predictions": [item.to_dict() for item in self.predictions],
            "uncertainty": list(self.uncertainty),
        }


@dataclass(frozen=True, slots=True)
class Goal:
    goal_id: str
    entity_id: str
    title: str
    status: str
    priority: int
    source_id: str
    evidence_ids: tuple[str, ...]
    created_at: str
    due_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "entity_id": self.entity_id,
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "source_id": self.source_id,
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
            "due_at": self.due_at,
        }


@dataclass(frozen=True, slots=True)
class Commitment:
    commitment_id: str
    entity_id: str
    title: str
    status: str
    source_id: str
    evidence_ids: tuple[str, ...]
    created_at: str
    due_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "commitment_id": self.commitment_id,
            "entity_id": self.entity_id,
            "title": self.title,
            "status": self.status,
            "source_id": self.source_id,
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
            "due_at": self.due_at,
        }


@dataclass(frozen=True, slots=True)
class Prediction:
    prediction_id: str
    entity_id: str
    predicate: str
    predicted_value: Any
    confidence: float
    predicted_at: str
    expected_by: str | None
    source_id: str
    evidence_ids: tuple[str, ...]
    verification_status: str = "pending"
    verified_at: str | None = None
    actual_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "entity_id": self.entity_id,
            "predicate": self.predicate,
            "predicted_value": self.predicted_value,
            "confidence": self.confidence,
            "predicted_at": self.predicted_at,
            "expected_by": self.expected_by,
            "source_id": self.source_id,
            "evidence_ids": list(self.evidence_ids),
            "verification_status": self.verification_status,
            "verified_at": self.verified_at,
            "actual_value": self.actual_value,
        }


@dataclass(frozen=True, slots=True)
class MemoryItem:
    memory_id: str
    layer: MemoryLayer
    content: str
    source_id: str
    evidence_ids: tuple[str, ...]
    confidence: float
    created_at: str
    expires_at: str | None = None
    status: str = "active"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "layer": enum_value(self.layer),
            "content": self.content,
            "source_id": self.source_id,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ImportCandidate:
    candidate_id: str
    source_id: str
    conversation_id: str
    candidate_type: str
    subject: str
    predicate: str
    value: str
    claim_class: ClaimClass
    information_kind: InformationKind
    confidence: float
    quote: str
    observed_at: str
    status: ReviewStatus = ReviewStatus.PENDING
    exclusion_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_id": self.source_id,
            "conversation_id": self.conversation_id,
            "candidate_type": self.candidate_type,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "claim_class": enum_value(self.claim_class),
            "information_kind": enum_value(self.information_kind),
            "confidence": self.confidence,
            "quote": self.quote,
            "observed_at": self.observed_at,
            "status": enum_value(self.status),
            "exclusion_reason": self.exclusion_reason,
        }


__all__ = [
    "Belief",
    "BeliefExplanation",
    "BeliefStatus",
    "ClaimClass",
    "Commitment",
    "Entity",
    "EntityKind",
    "Evidence",
    "EvidenceRole",
    "Goal",
    "ImportCandidate",
    "InformationKind",
    "MemoryItem",
    "MemoryLayer",
    "Observation",
    "Prediction",
    "Provenance",
    "ReviewStatus",
    "SourceStatus",
    "WorldSource",
    "canonical_json",
    "enum_value",
    "parse_timestamp",
    "stable_id",
    "utc_iso",
]
