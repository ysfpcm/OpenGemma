"""Fail-closed Phase 10 candidate lifecycle."""

from __future__ import annotations

from .models import CandidateStatus


class CandidateLifecycleError(ValueError):
    """Raised when a candidate skips a required promotion state."""


LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    CandidateStatus.CANDIDATE.value: frozenset(
        {CandidateStatus.SANDBOXED.value, CandidateStatus.REJECTED.value}
    ),
    CandidateStatus.SANDBOXED.value: frozenset(
        {CandidateStatus.REPLAY_PASSED.value, CandidateStatus.REJECTED.value}
    ),
    CandidateStatus.REPLAY_PASSED.value: frozenset(
        {CandidateStatus.SHADOW.value, CandidateStatus.REJECTED.value}
    ),
    CandidateStatus.SHADOW.value: frozenset(
        {CandidateStatus.APPROVED.value, CandidateStatus.REJECTED.value}
    ),
    CandidateStatus.APPROVED.value: frozenset({CandidateStatus.STAGED.value}),
    CandidateStatus.STAGED.value: frozenset(
        {CandidateStatus.ACTIVE.value, CandidateStatus.REJECTED.value}
    ),
    CandidateStatus.ACTIVE.value: frozenset({CandidateStatus.ROLLED_BACK.value}),
    CandidateStatus.REJECTED.value: frozenset(),
    CandidateStatus.ROLLED_BACK.value: frozenset(),
}


def validate_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in LEGAL_TRANSITIONS.get(current, frozenset()):
        raise CandidateLifecycleError(
            f"illegal Phase 10 candidate transition {current!r} -> {target!r}"
        )


__all__ = ["CandidateLifecycleError", "LEGAL_TRANSITIONS", "validate_transition"]
