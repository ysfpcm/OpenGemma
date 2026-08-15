"""Prompt contract for the model that sits before the deterministic resolver."""

from __future__ import annotations

import json
from typing import Sequence

from openjarvis.behavior.catalog import INTENT_CATALOG, intent_schema
from openjarvis.behavior.models import BehaviorEntity

_ACTION_LINES = "\n".join(
    f"- {spec.action}: {spec.description} ({', '.join(spec.domains)})."
    for spec in INTENT_CATALOG.values()
)

BEHAVIOR_EXTRACTION_INSTRUCTIONS = f"""You extract a possible Home Assistant command
from natural speech.
Return JSON only with this shape:
{json.dumps(intent_schema(), indent=2)}

Supported actions:
{_ACTION_LINES}

Rules:
- Use entity_id only when it is present in the supplied context; never invent one.
- Resolve "it", "that light", "the lamp", and similar references from the
  recent topic only when that topic is supplied.
- If two devices could match, leave entity_id null and let the resolver ask one short question.
- Put numeric or mode values in parameters, using the catalog parameter names.
- Treat typos, shorthand, follow-ups, and voice-transcription quirks as normal speech.
- Never invent a service name, domain, entity id, parameter, or value that is not
  supported by the catalog or present in the user request.
- The resolver, not the model, decides whether a command is safe to execute
  and Home Assistant verifies the result.
"""


def build_behavior_prompt(
    entities: Sequence[BehaviorEntity],
    *,
    recent_topic: BehaviorEntity | None = None,
) -> str:
    """Build a compact context block for an intent/entity extraction call."""
    lines = [BEHAVIOR_EXTRACTION_INSTRUCTIONS.rstrip(), "", "Known devices:"]
    if entities:
        for entity in entities:
            aliases = ", ".join(entity.aliases) if entity.aliases else "none"
            lines.append(
                f"- {entity.entity_id}: {entity.name}; "
                f"area={entity.area or 'unknown'}; aliases={aliases}"
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            f"Recent topic: {recent_topic.entity_id if recent_topic else 'none'}",
        ]
    )
    return "\n".join(lines)


__all__ = ["BEHAVIOR_EXTRACTION_INSTRUCTIONS", "build_behavior_prompt"]
