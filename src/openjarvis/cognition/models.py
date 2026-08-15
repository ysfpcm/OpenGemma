"""Versioned, model-independent cognition contracts."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Optional, Type, TypeVar

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContractError(ValueError):
    pass


class ContractCompatibilityError(ContractError):
    pass


ContractMigrationError = ContractCompatibilityError


class UnsupportedSchemaVersion(ContractCompatibilityError):
    pass
T = TypeVar("T", bound="CognitionContract")


@dataclass
class CognitionContract:
    """Common causal envelope used by every persisted cognition object."""

    contract_type: ClassVar[str] = "contract"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)
    valid_from: str = field(default_factory=utc_now)
    valid_until: Optional[str] = None
    provenance: dict[str, Any] = field(default_factory=dict)
    causal_parents: list[str] = field(default_factory=list)
    sensitivity_labels: list[str] = field(default_factory=list)
    taint_labels: list[str] = field(default_factory=list)
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ContractCompatibilityError(
                f"incompatible {self.contract_type} schema version "
                f"{self.schema_version}; "
                f"supported version is {SCHEMA_VERSION}"
            )
        if not self.id:
            raise ContractError("contract id must not be empty")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ContractError("confidence must be between 0 and 1")
        for name in ("created_at", "valid_from"):
            try:
                datetime.fromisoformat(getattr(self, name))
            except (TypeError, ValueError) as exc:
                raise ContractError(f"{name} must be an ISO-8601 timestamp") from exc

    def to_dict(self) -> dict[str, Any]:
        return {"contract_type": self.contract_type, **asdict(self)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls: Type[T], value: dict[str, Any]) -> T:
        raw = _migrate(dict(value))
        discriminator = raw.pop("contract_type")
        target = CONTRACT_TYPES.get(discriminator)
        if target is None:
            raise ContractCompatibilityError(
                f"incompatible or unknown contract type {discriminator!r}"
            )
        if cls is not CognitionContract and target is not cls:
            raise ContractCompatibilityError(
                f"incompatible contract: expected {cls.contract_type}, "
                f"got {discriminator}"
            )
        allowed = {item.name for item in fields(target)}
        extra = set(raw) - allowed
        if extra:
            raise ContractCompatibilityError(
                f"incompatible fields for {discriminator}: {sorted(extra)}"
            )
        return target(**raw)  # type: ignore[return-value]

    @classmethod
    def from_json(cls: Type[T], value: str) -> T:
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ContractCompatibilityError("incompatible contract JSON") from exc
        if not isinstance(raw, dict):
            raise ContractCompatibilityError("incompatible contract JSON root")
        return cls.from_dict(raw)


@dataclass
class Observation(CognitionContract):
    contract_type: ClassVar[str] = "observation"
    subject: str = ""
    value: Any = None
    source_kind: str = "observed"


@dataclass
class Belief(CognitionContract):
    contract_type: ClassVar[str] = "belief"
    statement: str = ""


@dataclass
class Goal(CognitionContract):
    contract_type: ClassVar[str] = "goal"
    objective: str = ""
    success_conditions: list[str] = field(default_factory=list)


@dataclass
class Commitment(CognitionContract):
    contract_type: ClassVar[str] = "commitment"
    promise: str = ""


@dataclass
class Situation(CognitionContract):
    contract_type: ClassVar[str] = "situation"
    situation_type: str = ""
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class Plan(CognitionContract):
    contract_type: ClassVar[str] = "plan"
    goal_id: str = ""
    action_ids: list[str] = field(default_factory=list)


@dataclass
class ActionProposal(CognitionContract):
    contract_type: ClassVar[str] = "action_proposal"
    action_type: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    expected_effect: dict[str, Any] = field(default_factory=dict)


@dataclass
class Authorization(CognitionContract):
    contract_type: ClassVar[str] = "authorization"
    action_id: str = ""
    decision: str = "authorized"
    authority: str = ""
    scope: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionAttempt(CognitionContract):
    contract_type: ClassVar[str] = "action_attempt"
    action_id: str = ""
    attempt_number: int = 1
    executor: str = ""
    tool_succeeded: Optional[bool] = None
    tool_response: Any = None
    error_kind: Optional[str] = None


@dataclass
class Verification(CognitionContract):
    contract_type: ClassVar[str] = "verification"
    action_id: str = ""
    verifier: str = ""
    observed_effect: Any = None
    succeeded: Optional[bool] = None
    error_kind: Optional[str] = None


@dataclass
class Prediction(CognitionContract):
    contract_type: ClassVar[str] = "prediction"
    claim: str = ""


@dataclass
class Episode(CognitionContract):
    contract_type: ClassVar[str] = "episode"
    title: str = ""
    event_ids: list[str] = field(default_factory=list)
    outcome: str = ""


@dataclass
class LearningCandidate(CognitionContract):
    contract_type: ClassVar[str] = "learning_candidate"
    candidate_kind: str = ""
    proposal: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionReceipt(CognitionContract):
    contract_type: ClassVar[str] = "decision_receipt"
    decision: str = ""
    rationale: str = ""
    evidence_ids: list[str] = field(default_factory=list)


CONTRACT_TYPES: dict[str, Type[CognitionContract]] = {
    item.contract_type: item
    for item in (
        Observation,
        Belief,
        Goal,
        Commitment,
        Situation,
        Plan,
        ActionProposal,
        Authorization,
        ActionAttempt,
        Verification,
        Prediction,
        Episode,
        LearningCandidate,
        DecisionReceipt,
    )
}


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    version = raw.get("schema_version")
    if version is None:
        timestamp = raw.pop("timestamp", None)
        raw["created_at"] = raw.get("created_at") or timestamp or utc_now()
        raw["valid_from"] = raw.get("valid_from") or raw["created_at"]
        raw["schema_version"] = SCHEMA_VERSION
    elif version != SCHEMA_VERSION:
        try:
            stored_major = int(str(version).split(".", 1)[0])
            supported_major = int(str(SCHEMA_VERSION).split(".", 1)[0])
        except ValueError:
            stored_major = -1
            supported_major = 1
        if stored_major > supported_major:
            raise UnsupportedSchemaVersion(
                f"stored schema version {version} is newer than "
                f"supported version {SCHEMA_VERSION}"
            )
        raise ContractCompatibilityError(
            f"incompatible stored schema version {version}; "
            f"supported version is {SCHEMA_VERSION}"
        )
    raw.setdefault("valid_until", None)
    raw.setdefault("provenance", {})
    raw.setdefault("causal_parents", [])
    raw.setdefault("sensitivity_labels", [])
    raw.setdefault("taint_labels", [])
    raw.setdefault("confidence", None)
    if not raw.get("contract_type"):
        raise ContractCompatibilityError(
            "incompatible contract: contract_type is missing"
        )
    return raw


class ContractStore:
    """SQLite round-trip store that preserves unrelated pre-existing tables."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cognition_contracts ("
            "id TEXT PRIMARY KEY, contract_type TEXT NOT NULL, "
            "schema_version TEXT NOT NULL, "
            "body_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cognition_schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO cognition_schema_migrations VALUES (1, ?)",
            (utc_now(),),
        )
        self._conn.commit()

    def put(self, contract: CognitionContract) -> None:
        self._conn.execute(
            "INSERT INTO cognition_contracts VALUES (?, ?, ?, ?, ?)",
            (
                contract.id,
                contract.contract_type,
                contract.schema_version,
                contract.to_json(),
                contract.created_at,
            ),
        )
        self._conn.commit()

    def get(self, contract_id: str) -> CognitionContract:
        row = self._conn.execute(
            "SELECT body_json FROM cognition_contracts WHERE id = ?", (contract_id,)
        ).fetchone()
        if row is None:
            raise KeyError(contract_id)
        return CognitionContract.from_json(row[0])

    def close(self) -> None:
        self._conn.close()


def restore_database(fixture: str | Path, destination: str | Path) -> None:
    """Restore a known pre-migration database byte-for-byte."""
    shutil.copy2(Path(fixture), Path(destination))
