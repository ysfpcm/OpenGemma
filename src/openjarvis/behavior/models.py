"""Public data types for natural-language behavior resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from openjarvis.behavior.catalog import SUPPORTED_ACTIONS
from openjarvis.behavior.normalization import normalize_text

BehaviorAction = str
ResolutionStatus = Literal["execute", "clarify", "unsupported"]


@dataclass(frozen=True, slots=True)
class BehaviorEntity:
    """A canonical device plus the names a person may use for it."""

    entity_id: str
    name: str
    domain: str = ""
    area: str = ""
    aliases: tuple[str, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        entity_id = str(self.entity_id or "").strip()
        name = str(self.name or "").strip()
        if not entity_id or "." not in entity_id:
            raise ValueError("entity_id must look like domain.object_id")
        if not name:
            raise ValueError("entity name cannot be empty")
        if not self.domain:
            object.__setattr__(self, "domain", entity_id.split(".", 1)[0])
        object.__setattr__(
            self,
            "aliases",
            tuple(str(alias).strip() for alias in self.aliases if str(alias).strip()),
        )

    @property
    def normalized_name(self) -> str:
        return normalize_text(self.name)


@dataclass(frozen=True, slots=True)
class ModelPrediction:
    """The only model output the behavior layer needs."""

    action: str | None = None
    entity_id: str | None = None
    entity_mention: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    rationale: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ModelPrediction":
        if not value:
            return cls()
        raw_confidence = value.get("confidence", 0.0)
        try:
            confidence = min(max(float(raw_confidence), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            action=str(value.get("action") or "").strip().lower() or None,
            entity_id=str(value.get("entity_id") or "").strip() or None,
            entity_mention=str(value.get("entity_mention") or "").strip() or None,
            parameters=(
                dict(value.get("parameters"))
                if isinstance(value.get("parameters"), Mapping)
                else {}
            ),
            confidence=confidence,
            rationale=str(value.get("rationale") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class BehaviorResolution:
    """A safe decision between executing a tool call and asking a question."""

    status: ResolutionStatus
    utterance: str
    normalized_utterance: str
    action: str | None = None
    entity_id: str | None = None
    entity_name: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    source: str = ""
    clarification: str | None = None
    evidence: tuple[str, ...] = field(default_factory=tuple)
    correction_id: int | None = None

    @property
    def should_execute(self) -> bool:
        return self.status == "execute"

    def tool_call(self) -> dict[str, Any] | None:
        """Return canonical tool parameters, or ``None`` when clarification is needed."""
        if not self.should_execute or not self.action:
            return None
        call: dict[str, Any] = {"action": self.action}
        if self.entity_id:
            call["entity"] = self.entity_id
        call.update({str(key): value for key, value in self.parameters.items()})
        return call


@dataclass(frozen=True, slots=True)
class CorrectionExample:
    """A before/after teaching event saved for future matching."""

    id: int
    before_text: str
    normalized_text: str
    corrected_action: str
    corrected_entity_id: str | None
    corrected_entity_name: str | None
    recent_topic_entity_id: str | None
    predicted_action: str | None
    predicted_entity_id: str | None
    corrected_parameters: Mapping[str, Any]
    predicted_parameters: Mapping[str, Any]
    context: Mapping[str, Any]
    created_at: str


__all__ = [
    "BehaviorAction",
    "BehaviorEntity",
    "BehaviorResolution",
    "CorrectionExample",
    "ModelPrediction",
    "ResolutionStatus",
    "SUPPORTED_ACTIONS",
]
