from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from openjarvis.agents._stubs import AgentResult
from openjarvis.agents.executor import AgentExecutor
from openjarvis.agents.manager import AgentManager
from openjarvis.context import ContextEvent, ContextStore
from openjarvis.core.events import EventBus


def test_managed_tick_reads_live_context_before_agent_run(tmp_path):
    manager = AgentManager(str(tmp_path / "agents.db"))
    context_store = ContextStore(tmp_path / "context.db")
    context_store.apply_event(
        ContextEvent(
            source_key="website:release-notes",
            source_type="website",
            external_event_id="check-1",
            external_entity_id="release-notes",
            entity_type="website_monitor",
            entity_name="Release notes",
            state={"changed": True, "headline": "Version 2 is available"},
        )
    )
    agent = manager.create_agent("watcher", config={"instruction": "Check updates"})
    system = SimpleNamespace(engine=object(), model="test-model", config=None)
    executor = AgentExecutor(
        manager, EventBus(), system=system, context_store=context_store
    )
    captured = {}

    class CapturingAgent:
        accepts_tools = False

        def __init__(self, engine, model, **kwargs):
            pass

        def run(self, input_text, context=None):
            captured["input"] = input_text
            captured["context"] = context
            return AgentResult(content="done")

    with patch("openjarvis.core.registry.AgentRegistry.get", return_value=CapturingAgent):
        executor._invoke_agent(agent)

    assert "Current context database snapshot:" in captured["input"]
    assert "Version 2 is available" in captured["input"]
    assert captured["context"].metadata["context_generated_at"]
    context_store.close()
    manager.close()
