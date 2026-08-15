"""Feature flags for subsystems introduced by the phased architecture."""

from __future__ import annotations

import os

PHASE_FEATURE_FLAGS = {
    phase: os.getenv(f"OPHANIM_PHASE_{phase}_ENABLED", "0") == "1"
    for phase in range(1, 13)
}

# Phase 0 contracts and truthful persistence are foundational and always on.
PHASE_FEATURE_FLAGS[0] = True


def phase_enabled(phase: int) -> bool:
    if phase not in PHASE_FEATURE_FLAGS:
        raise ValueError(f"unknown implementation phase: {phase}")
    return PHASE_FEATURE_FLAGS[phase]
