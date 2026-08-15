"""Truthful action lifecycle definitions and transition validation."""

from __future__ import annotations

from enum import Enum


class ActionState(str, Enum):
    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    EXECUTING = "executing"
    EFFECT_PENDING = "effect_pending"
    VERIFIED = "verified"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    NEEDS_ATTENTION = "needs_attention"


class ActionErrorCode(str, Enum):
    DENIED = "denied"
    INVALID = "invalid"
    STALE = "stale"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    AMBIGUOUS_EFFECT = "ambiguous-effect"
    VERIFICATION_FAILED = "verification-failed"
    CANCELED = "canceled"


class IllegalActionTransition(ValueError):
    pass


LEGAL_TRANSITIONS = {
    ActionState.PROPOSED: {ActionState.AUTHORIZED, ActionState.FAILED},
    ActionState.AUTHORIZED: {ActionState.EXECUTING, ActionState.FAILED},
    ActionState.EXECUTING: {
        ActionState.EFFECT_PENDING,
        ActionState.FAILED,
        ActionState.NEEDS_ATTENTION,
    },
    ActionState.EFFECT_PENDING: {
        ActionState.VERIFIED,
        ActionState.FAILED,
        ActionState.NEEDS_ATTENTION,
        ActionState.COMPENSATING,
    },
    ActionState.NEEDS_ATTENTION: {
        ActionState.EFFECT_PENDING,
        ActionState.VERIFIED,
        ActionState.FAILED,
        ActionState.COMPENSATING,
    },
    ActionState.COMPENSATING: {
        ActionState.COMPENSATED,
        ActionState.NEEDS_ATTENTION,
        ActionState.FAILED,
    },
    ActionState.VERIFIED: set(),
    ActionState.FAILED: {ActionState.COMPENSATING},
    ActionState.COMPENSATED: set(),
}


def validate_transition(current: ActionState, target: ActionState) -> None:
    if target not in LEGAL_TRANSITIONS[current]:
        raise IllegalActionTransition(
            f"illegal action transition: {current.value} -> {target.value}"
        )
