"""Tests for the local-first auto model policy."""

from __future__ import annotations

from types import SimpleNamespace

from openjarvis.server.models import ChatCompletionRequest
from openjarvis.server.routes import _resolve_auto_model


def _request(text: str) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="auto",
        messages=[{"role": "user", "content": text}],
    )


def test_auto_routing_keeps_short_requests_local(monkeypatch) -> None:
    monkeypatch.setattr(
        "openjarvis.server.cloud_router._load_keys",
        lambda: {"OPENAI_API_KEY": "test-key"},
    )
    engine = SimpleNamespace(list_models=lambda: ["qwen3.5:9b"])

    model, reason, _ = _resolve_auto_model(_request("Hi"), engine)

    assert model == "qwen3.5:9b"
    assert reason == "local_default"


def test_auto_routing_uses_gpt_for_moderate_reasoning(monkeypatch) -> None:
    monkeypatch.setattr(
        "openjarvis.server.cloud_router._load_keys",
        lambda: {"OPENAI_API_KEY": "test-key"},
    )
    engine = SimpleNamespace(list_models=lambda: ["qwen3.5:9b"])

    model, reason, complexity = _resolve_auto_model(
        _request("Solve the integral and explain why step by step, then compare the alternatives."),
        engine,
    )

    assert complexity is not None and complexity.score >= 0.30
    assert model == "gpt-5-mini"
    assert reason == "advanced_reasoning"


def test_auto_routing_falls_back_to_qwen_without_openai(monkeypatch) -> None:
    monkeypatch.setattr("openjarvis.server.cloud_router._load_keys", lambda: {})
    engine = SimpleNamespace(list_models=lambda: ["qwen3.5:9b"])

    model, reason, _ = _resolve_auto_model(
        _request("Solve the integral and explain why step by step, then compare the alternatives."),
        engine,
    )

    assert model == "qwen3.5:9b"
    assert reason == "cloud_unavailable"
