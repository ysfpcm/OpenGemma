"""Natural-language delegation from OpenJarvis to local Home Assistant.

The coordinator submits the user's original instruction unchanged. The local
OpenJarvis orchestrator decides whether the Home Assistant tool is needed and
runs the normal tool-calling loop.
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openjarvis.agents._stubs import AgentResult
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import Message, Role
from openjarvis.tools.home_assistant import HomeAssistantCommandTool, get_alexa_devices

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/home-assistant", tags=["home-assistant"])

_EXECUTOR_PROMPT = """You are OpenJarvis's local smart-home agent.
Use the Home Assistant tool when the user asks to control a configured device.
Preserve the user's newest intent and do not invent devices or actions.
The tool is confirmation-gated; never claim an action happened unless its tool
result says it succeeded. When the user corrects themselves, follow the final
instruction only. Keep simple local-home requests fast and concise.
If the user corrects themselves, discard the superseded request. When specific
audio is requested for a sleeping speaker, Emit only that playback command.
"""

_LEGACY_EXECUTOR_PROMPT = """You are OpenJarvis's local smart-home executor.
For every actionable speaker request, call home_assistant_execute exactly once.
Return an ordered command for each requested action and nothing else.
Each command is an ordinary Alexa phrase, so it can cover music, volume,
lights, routines, timers, and other Alexa-connected devices. Use only the
configured speaker targets stated below. Preserve the user's intent, do not
invent devices, and do not perform anything beyond the instruction.

When the user corrects themselves (for example, 'actually', 'scratch that',
or 'instead'), discard the superseded request and follow the final intent.
When a speaker is off and the user asks to turn it on or play specific audio,
prefer the specific playback request because playing the audio wakes the
speaker. Emit only that playback command, not a separate power-on command;
use the requested media phrase verbatim where practical."""

_BEDROOM_WORDS = re.compile(
    r"\bbedroom\b|mav(?:'s|’s)?\s+bedroom|bedroom\s+speaker", re.I
)


def _allowed_targets(
    instruction: str, devices: dict[str, dict[str, str]]
) -> set[str]:
    """Make protected speaker access explicit and request-scoped."""
    allowed = set(devices)
    if "bedroom" in devices and not _BEDROOM_WORDS.search(instruction):
        allowed.remove("bedroom")
    return allowed


def _executor_tool(targets: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "home_assistant_execute",
            "description": "Execute a short ordered list of Alexa commands on configured speakers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "commands": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "properties": {
                                "target": {"type": "string", "enum": list(targets)},
                                "text_command": {"type": "string"},
                            },
                            "required": ["target", "text_command"],
                        },
                    },
                },
                "required": ["commands"],
            },
        },
    }


class HomeAssistantDelegateRequest(BaseModel):
    """Raw instruction forwarded unchanged from a coordinating agent."""

    instruction: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="default", min_length=1, max_length=128)
    context: list[str] = Field(default_factory=list, max_length=12)
    confirmed: bool = False


def _plan_instruction(
    engine: Any,
    model: str,
    instruction: str,
    targets: tuple[str, ...],
    context: list[str] | None = None,
) -> list[dict[str, str]]:
    """Ask the local model to turn raw language into a validated command list."""
    messages = [
        Message(
            role=Role.SYSTEM,
            content=f"{_EXECUTOR_PROMPT}\nConfigured speaker targets: {', '.join(targets)}.",
        )
    ]
    if context:
        recent_context = "\n".join(f"- {item}" for item in context[-12:])
        messages.append(
            Message(
                role=Role.SYSTEM,
                content=(
                    "Recent user context is provided only to resolve references "
                    "such as 'that', 'instead', or a room change. The newest user "
                    f"instruction remains authoritative.\n{recent_context}"
                ),
            )
        )
    messages.append(Message(role=Role.USER, content=instruction))
    result = engine.generate(
        messages,
        model=model,
        temperature=0.0,
        max_tokens=320,
        tools=[_executor_tool(targets)],
    )
    calls = result.get("tool_calls", []) if isinstance(result, dict) else []
    if len(calls) != 1 or calls[0].get("name") != "home_assistant_execute":
        raise ValueError("The local executor did not produce a valid command plan.")
    try:
        arguments = json.loads(calls[0].get("arguments", "{}"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("The local executor returned invalid command data.") from exc
    commands = arguments.get("commands") if isinstance(arguments, dict) else None
    if not isinstance(commands, list) or not commands or len(commands) > 5:
        raise ValueError("The local executor returned an invalid command list.")

    validated: list[dict[str, str]] = []
    for command in commands:
        if not isinstance(command, dict):
            raise ValueError("The local executor returned an invalid command.")
        target = str(command.get("target", "")).strip().lower()
        text_command = str(command.get("text_command", "")).strip()
        if target not in targets or not text_command:
            raise ValueError("The local executor requested an unsupported speaker command.")
        validated.append({"target": target, "text_command": text_command})
    return _normalize_commands(validated)


def _normalize_commands(commands: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove redundant power-on steps before a later playback request.

    Alexa wakes a sleeping speaker when it starts requested media.  Smaller
    local models sometimes emit both commands even after being instructed to
    prefer playback, so enforce that harmless normalization deterministically.
    """
    normalized: list[dict[str, str]] = []
    for index, command in enumerate(commands):
        phrase = command["text_command"].lower().strip()
        is_generic_power_on = phrase in {
            "turn on the speaker",
            "turn on the kitchen speaker",
            "turn on the bedroom speaker",
            "turn on the speakers",
        }
        later_playback = any(
            later["target"] == command["target"]
            and later["text_command"].lower().strip().startswith("play ")
            for later in commands[index + 1 :]
        )
        if is_generic_power_on and later_playback:
            continue
        normalized.append(command)
    return normalized


def _session_context(app: Any, session_id: str, supplied: list[str]) -> list[str]:
    """Combine caller-provided context with a small local session history."""
    histories = getattr(app.state, "home_assistant_histories", None)
    if histories is None:
        histories = {}
        app.state.home_assistant_histories = histories
    history = histories.setdefault(session_id, deque(maxlen=12))
    combined = [str(item).strip() for item in supplied if str(item).strip()]
    combined.extend(item for item in history if item not in combined)
    return combined[-12:]


def _remember_instruction(app: Any, session_id: str, instruction: str) -> None:
    histories = getattr(app.state, "home_assistant_histories", None)
    if histories is None:
        histories = {}
        app.state.home_assistant_histories = histories
    histories.setdefault(session_id, deque(maxlen=12)).append(instruction)


@router.post("/delegate")
def delegate_home_assistant_command(
    body: HomeAssistantDelegateRequest, request: Request
) -> dict[str, Any]:
    """Let OpenJarvis plan and execute a confirmed raw smart-home instruction."""
    model = getattr(request.app.state, "model", "")
    engine = getattr(request.app.state, "engine", None)
    if engine is None or not model:
        raise HTTPException(status_code=503, detail="Local executor is unavailable.")
    devices = get_alexa_devices()
    targets = _allowed_targets(body.instruction, devices)
    if not targets:
        raise HTTPException(status_code=503, detail="No local Alexa speakers are configured.")
    context = _session_context(request.app, body.session_id, body.context)
    _remember_instruction(request.app, body.session_id, body.instruction)
    system_prompt = (
        f"{_EXECUTOR_PROMPT}\nAllowed Alexa targets for this request: "
        f"{', '.join(sorted(targets))}."
    )
    if context:
        system_prompt += (
            "\nRecent context may resolve references, but the current instruction "
            "is authoritative.\nRecent context:\n"
            + "\n".join(f"- {item}" for item in context)
        )
    try:
        agent = OrchestratorAgent(
            engine,
            model,
            tools=[
                HomeAssistantCommandTool(
                    allowed_alexa_targets=targets,
                    allow_bedroom="bedroom" in targets,
                )
            ],
            max_turns=4,
            max_tokens=512,
            temperature=0.0,
            system_prompt=system_prompt,
            interactive=True,
            confirm_callback=lambda _prompt: body.confirmed,
        )
        result: AgentResult = agent.run(body.instruction)
    except Exception as exc:
        logger.warning("Home Assistant delegation failed: %s", exc)
        raise HTTPException(
            status_code=422, detail="Local Home Assistant delegation failed."
        ) from exc

    tool_results = [
        {"success": item.success, "content": item.content, "metadata": item.metadata}
        for item in result.tool_results
    ]
    executed = any(item.success for item in result.tool_results)
    return {
        "status": "completed" if body.confirmed and executed else "confirmation_required",
        "instruction": body.instruction,
        "response": result.content,
        "context_used": len(context),
        "results": tool_results,
        "message": (
            result.content
            if body.confirmed
            else "Set confirmed to true to let OpenJarvis execute this device command."
        ),
    }


__all__ = ["router"]
