"""Versioned, provenance-rich records used by the Phase 4 slice."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

EVENT_SCHEMA_VERSION = 1


class EventQuality(str, Enum):
    OBSERVED = "observed"
    REPORTED = "reported"
    INFERRED = "inferred"
    CONTRADICTED = "contradicted"


class SituationStatus(str, Enum):
    ACTIVE = "active"
    UNCERTAIN = "uncertain"
    CLOSED = "closed"


def utc_iso(value: datetime | str | None = None) -> str:
    """Return a canonical, timezone-aware ISO timestamp."""

    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        parsed = value
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


_SENSITIVE_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "auth",
)


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            any(marker in str(key).lower() for marker in _SENSITIVE_KEY_MARKERS)
            or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class DurableEvent:
    """A normalized event whose identity is stable across delivery retries."""

    event_type: str
    source: str
    observed_at: str
    payload: dict[str, Any]
    source_event_id: str = ""
    event_id: str = ""
    schema_version: int = EVENT_SCHEMA_VERSION
    ingested_at: str = field(default_factory=utc_iso)
    provenance: dict[str, Any] = field(default_factory=dict)
    sensitivity_labels: tuple[str, ...] = ()
    taint_labels: tuple[str, ...] = ()
    quality: EventQuality = EventQuality.OBSERVED
    raw_payload: dict[str, Any] | None = None
    identity_key: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported durable event schema version {self.schema_version}"
            )
        if not self.event_type.strip() or not self.source.strip():
            raise ValueError("event_type and source are required")
        if not isinstance(self.payload, dict):
            raise TypeError("normalized event payload must be an object")
        observed = utc_iso(self.observed_at)
        ingested = utc_iso(self.ingested_at)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "ingested_at", ingested)
        if not isinstance(self.quality, EventQuality):
            object.__setattr__(self, "quality", EventQuality(str(self.quality)))
        labels = tuple(str(item) for item in self.sensitivity_labels)
        taint = tuple(str(item) for item in self.taint_labels)
        object.__setattr__(self, "sensitivity_labels", labels)
        object.__setattr__(self, "taint_labels", taint)
        identity = self.identity_key or self._make_identity_key()
        event_id = (
            self.event_id or f"evt_{hashlib.sha256(identity.encode()).hexdigest()}"
        )
        object.__setattr__(self, "identity_key", identity)
        object.__setattr__(self, "event_id", event_id)

    def _make_identity_key(self) -> str:
        if self.source_event_id:
            return f"source:{self.source}:{self.source_event_id}"
        digest = hashlib.sha256(
            canonical_json(
                {
                    "event_type": self.event_type,
                    "source": self.source,
                    "observed_at": utc_iso(self.observed_at),
                    "payload": self.payload,
                }
            ).encode("utf-8")
        ).hexdigest()
        return f"content:{digest}"

    @property
    def sensitive(self) -> bool:
        labels = {label.lower() for label in self.sensitivity_labels}
        return bool(labels & {"sensitive", "restricted", "secret", "credential"})

    def normalized_payload(self) -> dict[str, Any]:
        return _redact_payload(self.payload)

    @property
    def has_sensitive_keys(self) -> bool:
        return _contains_sensitive_key(self.payload)

    @property
    def raw_payload_has_sensitive_keys(self) -> bool:
        return _contains_sensitive_key(self.raw_payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "identity_key": self.identity_key,
            "event_type": self.event_type,
            "source": self.source,
            "source_event_id": self.source_event_id,
            "schema_version": self.schema_version,
            "observed_at": self.observed_at,
            "ingested_at": self.ingested_at,
            "payload": self.normalized_payload(),
            "provenance": self.provenance,
            "sensitivity_labels": list(self.sensitivity_labels),
            "taint_labels": list(self.taint_labels),
            "quality": self.quality.value,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DurableEvent":
        raw = dict(value)
        raw["payload"] = dict(raw.get("payload") or {})
        raw["sensitivity_labels"] = tuple(raw.get("sensitivity_labels") or ())
        raw["taint_labels"] = tuple(raw.get("taint_labels") or ())
        raw["quality"] = EventQuality(raw.get("quality", EventQuality.OBSERVED))
        raw.pop("raw_payload", None)
        return cls(**{key: raw[key] for key in cls.__dataclass_fields__ if key in raw})


@dataclass(frozen=True, slots=True)
class JournalAppendResult:
    event: DurableEvent
    inserted: bool


@dataclass(frozen=True, slots=True)
class ConsumerDelivery:
    consumer: str
    worker_id: str
    event: DurableEvent
    attempts: int
    lease_until: float
