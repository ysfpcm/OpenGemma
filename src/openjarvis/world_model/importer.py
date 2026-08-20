"""Deterministic, review-first personal-history importer for Phase 5.

The importer treats conversation text as untrusted data.  It extracts only a
small, reviewable vocabulary and has no reference to Guardian, action, tool,
executor, or permission APIs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from openjarvis.world_model.models import (
    ClaimClass,
    ImportCandidate,
    InformationKind,
    ReviewStatus,
    stable_id,
    utc_iso,
)
from openjarvis.world_model.store import WorldModelStore
from openjarvis.world_model.world import LivingWorldModel

_SENSITIVE_TERMS = (
    "password",
    "passcode",
    "api key",
    "secret",
    "access token",
    "refresh token",
    "credential",
    "social security",
    "ssn",
    "credit card",
    "bank account",
    "medical",
    "diagnosis",
    "health record",
)
_INJECTION_TERMS = (
    "ignore previous instructions",
    "ignore all instructions",
    "grant guardian",
    "approve this action",
    "disable guardian",
    "run this command",
)


@dataclass(frozen=True, slots=True)
class HistoryConversation:
    conversation_id: str
    title: str
    created_at: str
    text: str
    category: str = "general"
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InventoryItem:
    conversation_id: str
    source_id: str
    title: str
    category: str
    created_at: str
    included: bool
    exclusion_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "conversation_id": self.conversation_id,
            "source_id": self.source_id,
            "title": self.title,
            "category": self.category,
            "created_at": self.created_at,
            "included": self.included,
            "exclusion_reason": self.exclusion_reason,
        }


class PersonalHistoryImporter:
    """Review-first importer with deterministic fixture extraction."""

    def __init__(
        self,
        store: WorldModelStore,
        *,
        excluded_categories: Iterable[str] = ("secrets", "health", "financial"),
    ) -> None:
        self.store = store
        self.world = LivingWorldModel(store)
        self.excluded_categories = {
            str(item).strip().casefold() for item in excluded_categories
        }

    @staticmethod
    def _source_id(conversation_id: str) -> str:
        return stable_id("history", conversation_id)

    def _exclusion_reason(self, conversation: HistoryConversation) -> str:
        category = conversation.category.strip().casefold()
        if category in self.excluded_categories:
            return f"sensitive category excluded: {category}"
        lowered = conversation.text.casefold()
        for term in _SENSITIVE_TERMS:
            if term in lowered:
                return f"sensitive content excluded: {term}"
        return ""

    def inventory(
        self, conversations: Iterable[HistoryConversation]
    ) -> list[InventoryItem]:
        result: list[InventoryItem] = []
        for conversation in conversations:
            source_id = self._source_id(conversation.conversation_id)
            reason = self._exclusion_reason(conversation)
            result.append(
                InventoryItem(
                    conversation.conversation_id,
                    source_id,
                    conversation.title,
                    conversation.category,
                    utc_iso(conversation.created_at),
                    not bool(reason),
                    reason,
                )
            )
        return result

    def ingest(self, conversation: HistoryConversation) -> list[ImportCandidate]:
        """Inventory one source and persist only candidates, never acceptance."""

        source_id = self._source_id(conversation.conversation_id)
        reason = self._exclusion_reason(conversation)
        created = utc_iso(conversation.created_at)
        self.store.save_history_source(
            source_id=source_id,
            conversation_id=conversation.conversation_id,
            title=conversation.title,
            category=conversation.category,
            created_at=created,
            content=conversation.text,
            excluded=bool(reason),
            exclusion_reason=reason,
        )
        if reason:
            return []
        candidates = self._extract(conversation, source_id=source_id)
        for candidate in candidates:
            self.store.add_candidate(candidate)
        return [self.store.get_candidate(item.candidate_id) for item in candidates]

    def _extract(
        self, conversation: HistoryConversation, *, source_id: str
    ) -> list[ImportCandidate]:
        text = conversation.text
        observed_at = utc_iso(conversation.created_at)
        # The importer deliberately uses a tiny deterministic grammar.  It is
        # not allowed to infer authority from arbitrary conversation prose.
        priority_pattern = re.compile(
            r"(?P<project>[A-Za-z][A-Za-z0-9 _-]{1,80}?)\s+is\s+"
            r"(?:(?:now|currently)\s+)?(?:the\s+)?priority\b",
            re.IGNORECASE,
        )
        hypothesis_pattern = re.compile(
            r"(?P<project>nodalUI|[A-Za-z][A-Za-z0-9 _-]{1,80}?)\s+"
            r"(?P<modal>might|may|could)\s+be\s+"
            r"(?P<verb>integrated|adopted|used)\b",
            re.IGNORECASE,
        )
        preference_pattern = re.compile(
            r"(?:I|Marc)\s+(?:prefer|likes?|want)\s+(?P<value>[^.!?\n]{2,100})",
            re.IGNORECASE,
        )
        value_pattern = re.compile(
            r"(?:I|Marc)\s+value\s+(?P<value>[^.!?\n]{2,100})",
            re.IGNORECASE,
        )
        decision_pattern = re.compile(
            r"(?:I|Marc)\s+decided\s+to\s+(?P<value>[^.!?\n]{2,100})",
            re.IGNORECASE,
        )
        candidates: list[ImportCandidate] = []

        def append_candidate(
            match: re.Match[str],
            *,
            candidate_type: str,
            predicate: str,
            value: str,
            claim_class: ClaimClass,
            confidence: float,
        ) -> None:
            quote = " ".join(match.group(0).strip().split())
            candidate_id = stable_id(
                "candidate", source_id, candidate_type, predicate, value, quote
            )
            candidates.append(
                ImportCandidate(
                    candidate_id=candidate_id,
                    source_id=source_id,
                    conversation_id=conversation.conversation_id,
                    candidate_type=candidate_type,
                    subject="Marc",
                    predicate=predicate,
                    value=" ".join(value.strip().split()),
                    claim_class=claim_class,
                    information_kind=InformationKind.REPORTED,
                    confidence=confidence,
                    quote=quote,
                    observed_at=observed_at,
                    status=ReviewStatus.PENDING,
                )
            )

        for match in priority_pattern.finditer(text):
            project = match.group("project").strip(" :-")
            if project.casefold().startswith("the "):
                project = project[4:].strip()
            lower_quote = match.group(0).casefold()
            correction = (
                "now" in lower_quote
                or "currently" in lower_quote
                or "correction" in text.casefold()
            )
            append_candidate(
                match,
                candidate_type="project",
                predicate="priority",
                value=project,
                claim_class=ClaimClass.FACT,
                confidence=0.98 if correction else 0.82,
            )
        for match in hypothesis_pattern.finditer(text):
            append_candidate(
                match,
                candidate_type="hypothesis",
                predicate="possible_integration",
                value=match.group("project").strip(),
                claim_class=ClaimClass.HYPOTHESIS,
                confidence=0.45,
            )
        for match in preference_pattern.finditer(text):
            append_candidate(
                match,
                candidate_type="preference",
                predicate="preference",
                value=match.group("value"),
                claim_class=ClaimClass.FACT,
                confidence=0.78,
            )
        for match in value_pattern.finditer(text):
            append_candidate(
                match,
                candidate_type="value",
                predicate="value",
                value=match.group("value"),
                claim_class=ClaimClass.FACT,
                confidence=0.78,
            )
        for match in decision_pattern.finditer(text):
            append_candidate(
                match,
                candidate_type="decision",
                predicate="decision",
                value=match.group("value"),
                claim_class=ClaimClass.FACT,
                confidence=0.8,
            )
        # A prompt-injection-looking conversation is retained only as
        # untrusted source text; it does not produce a candidate or capability.
        if any(term in text.casefold() for term in _INJECTION_TERMS):
            return []
        return candidates

    def review(self, candidate_id: str, decision: str) -> ImportCandidate:
        normalized = decision.strip().casefold()
        mapping = {
            "accept": ReviewStatus.ACCEPTED,
            "reject": ReviewStatus.REJECTED,
            "expire": ReviewStatus.EXPIRED,
        }
        if normalized not in mapping:
            raise ValueError("decision must be accept, reject, or expire")
        candidate = self.store.get_candidate(candidate_id)
        if candidate.status is not ReviewStatus.PENDING:
            raise ValueError(f"candidate is already {candidate.status.value}")
        if mapping[normalized] is ReviewStatus.ACCEPTED:
            source = self.store.get_source(candidate.source_id)
            correction = (
                "now" in candidate.quote.casefold()
                or "currently" in candidate.quote.casefold()
            )
            observation = self.world.observe(
                source_id=candidate.source_id,
                source_type=source.source_type,
                entity_kind="person",
                entity_name=candidate.subject,
                predicate=candidate.predicate,
                value=candidate.value,
                information_kind=candidate.information_kind,
                claim_class=candidate.claim_class,
                confidence=candidate.confidence,
                observed_at=candidate.observed_at,
                recorded_at=candidate.observed_at,
                evidence_text=candidate.quote,
                provenance={
                    "source_ref": (
                        f"{candidate.conversation_id}:{candidate.candidate_id}"
                    ),
                    "transformation": "deterministic reviewed history extraction",
                    "extractor": "phase5-fixture-v1",
                },
                taint_labels=("untrusted_import",),
                correction=correction,
            )
            # Accepted facts are semantic memory. Hypotheses go to semantic
            # memory too, but remain typed as hypotheses in the world model.
            self.world_model_memory().write(
                f"{candidate.predicate}: {candidate.value}",
                source_id=candidate.source_id,
                evidence_ids=(observation.observation_id,),
                confidence=candidate.confidence,
                created_at=candidate.observed_at,
                metadata={
                    "claim_class": candidate.claim_class.value,
                    "candidate_id": candidate.candidate_id,
                },
            )
        # Mark acceptance only after all derived records are durable. If a
        # write fails, the pending candidate can be retried without claiming
        # that review completed.
        self.store.update_candidate_status(candidate_id, mapping[normalized])
        return self.store.get_candidate(candidate_id)

    def world_model_memory(self):
        from openjarvis.world_model.world import LayeredMemory

        return LayeredMemory(self.store).semantic_memory

    def accept(self, candidate_id: str) -> ImportCandidate:
        return self.review(candidate_id, "accept")

    def reject(self, candidate_id: str) -> ImportCandidate:
        return self.review(candidate_id, "reject")

    def expire(self, candidate_id: str) -> ImportCandidate:
        return self.review(candidate_id, "expire")

    def correct(
        self, candidate_id: str, *, value: str, observed_at: str | None = None
    ) -> ImportCandidate:
        candidate = self.store.get_candidate(candidate_id)
        if candidate.status is not ReviewStatus.ACCEPTED:
            raise ValueError("only an accepted candidate can be corrected")
        correction_time = observed_at or utc_iso()
        self.world.direct_correction(
            entity_kind="person",
            entity_name=candidate.subject,
            predicate=candidate.predicate,
            value=value,
            source_id=f"correction:{candidate.candidate_id}",
            observed_at=correction_time,
            recorded_at=correction_time,
            evidence_text=f"Correction to {candidate.candidate_id}",
        )
        return candidate

    def export(self, source_id: str) -> dict[str, object]:
        return self.store.export_source(source_id)

    def delete(self, source_id: str) -> dict[str, int | str]:
        return self.store.tombstone_source(source_id)

    def cleanup_provenance(self, source_id: str) -> dict[str, int | str]:
        return self.delete(source_id)


__all__ = ["HistoryConversation", "InventoryItem", "PersonalHistoryImporter"]
