"""Phase 10 Learning Laboratory: controlled learning without hidden mutation."""

from .fixtures import (
    CodexIsolationResult,
    HistoricalMissionFixture,
    HomeAssistantLearningFixture,
    copy_bounded_codex_workspace,
    digest_tree,
    historical_codex_mission_fixtures,
)
from .lifecycle import LEGAL_TRANSITIONS, CandidateLifecycleError
from .models import *  # noqa: F403
from .models import __all__ as _MODEL_EXPORTS
from .service import (
    ApprovalRequiredError,
    ArtifactIntegrityError,
    AuthorityBoundaryError,
    ConsentRequiredError,
    LearningLaboratory,
    LearningLaboratoryError,
    PrivacyViolationError,
)
from .store import LaboratoryStore

__all__ = [
    "ApprovalRequiredError",
    "ArtifactIntegrityError",
    "AuthorityBoundaryError",
    "CandidateLifecycleError",
    "CodexIsolationResult",
    "ConsentRequiredError",
    "HistoricalMissionFixture",
    "HomeAssistantLearningFixture",
    "LEGAL_TRANSITIONS",
    "LaboratoryStore",
    "LearningLaboratory",
    "LearningLaboratoryError",
    "PrivacyViolationError",
    "copy_bounded_codex_workspace",
    "digest_tree",
    "historical_codex_mission_fixtures",
    *_MODEL_EXPORTS,
]
