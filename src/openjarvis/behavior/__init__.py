"""Natural-language behavior training and safe command resolution."""

from openjarvis.behavior.evals import (
    BehaviorEvalCase,
    BehaviorEvalReport,
    BehaviorEvalResult,
    entity_from_mapping,
    evaluate_cases,
    load_cases,
)
from openjarvis.behavior.context import entities_from_snapshot
from openjarvis.behavior.dataset import generate_seed_examples, write_seed_dataset
from openjarvis.behavior.catalog import (
    INTENT_CATALOG,
    IntentSpec,
    SUPPORTED_ACTIONS,
    get_intent_spec,
    intent_schema,
)
from openjarvis.behavior.models import (
    BehaviorEntity,
    BehaviorResolution,
    CorrectionExample,
    ModelPrediction,
)
from openjarvis.behavior.prompt import BEHAVIOR_EXTRACTION_INSTRUCTIONS, build_behavior_prompt
from openjarvis.behavior.resolver import BehaviorResolver
from openjarvis.behavior.store import BehaviorStore, CorrectionMatch

__all__ = [
    "BEHAVIOR_EXTRACTION_INSTRUCTIONS",
    "INTENT_CATALOG",
    "IntentSpec",
    "SUPPORTED_ACTIONS",
    "BehaviorEntity",
    "BehaviorEvalCase",
    "BehaviorEvalReport",
    "BehaviorEvalResult",
    "BehaviorResolution",
    "BehaviorResolver",
    "BehaviorStore",
    "CorrectionExample",
    "CorrectionMatch",
    "ModelPrediction",
    "build_behavior_prompt",
    "get_intent_spec",
    "entity_from_mapping",
    "entities_from_snapshot",
    "evaluate_cases",
    "generate_seed_examples",
    "load_cases",
    "intent_schema",
    "write_seed_dataset",
]
