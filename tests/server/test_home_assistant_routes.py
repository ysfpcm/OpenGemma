"""Tests for local natural-language Home Assistant delegation."""

from __future__ import annotations

from openjarvis.server.home_assistant_routes import _normalize_commands, _plan_instruction


class _FakeEngine:
    def generate(self, messages, **kwargs):
        return {
            "tool_calls": [
                {
                    "name": "home_assistant_execute",
                    "arguments": (
                        '{"commands":[{"target":"kitchen","text_command":"stop"},'
                        '{"target":"bedroom","text_command":"play rain sounds"}]}'
                    ),
                }
            ]
        }


def test_plans_an_ordered_speaker_command_list() -> None:
    commands = _plan_instruction(
        _FakeEngine(),
        "qwen3.5:2b",
        "Turn off rain sounds on the kitchen speaker, and play rain sounds on the bedroom speaker.",
        ("bedroom", "kitchen"),
    )

    assert commands == [
        {"target": "kitchen", "text_command": "stop"},
        {"target": "bedroom", "text_command": "play rain sounds"},
    ]


def test_passes_recent_context_to_the_local_executor() -> None:
    engine = _FakeEngine()
    _plan_instruction(
        engine,
        "qwen3.5:2b",
        "Use the kitchen speaker this time.",
        ("bedroom", "kitchen"),
        ["Play rain sounds."],
    )


def test_executor_prompt_handles_corrections_and_specific_playback() -> None:
    from openjarvis.server.home_assistant_routes import _EXECUTOR_PROMPT

    assert "discard the superseded request" in _EXECUTOR_PROMPT
    assert "Emit only that playback command" in _EXECUTOR_PROMPT


def test_removes_redundant_power_on_before_playback() -> None:
    assert _normalize_commands(
        [
            {"target": "kitchen", "text_command": "turn on the kitchen speaker"},
            {"target": "kitchen", "text_command": "play rain sounds"},
        ]
    ) == [{"target": "kitchen", "text_command": "play rain sounds"}]
