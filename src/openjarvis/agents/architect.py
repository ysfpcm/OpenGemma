"""
Architect Agent (OpenJarvis Extension)

This agent acts as the autonomous builder and coding partner. It uses a digital whiteboard
interface (via OpenJarvis UI) to collaboratively plan and write code using artifacts
like Implementation Plans and Walkthroughs.
"""

import logging
from typing import Any, Optional

from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
from openjarvis.core.registry import AgentRegistry
from openjarvis.engine._stubs import InferenceEngine

logger = logging.getLogger(__name__)

@AgentRegistry.register("architect")
class ArchitectAgent(BaseAgent):
    agent_id = "architect"

    def __init__(
        self,
        engine: InferenceEngine,
        model: str,
        **kwargs: Any
    ):
        super().__init__(engine, model, **kwargs)
        self.artifacts = {}

    def create_artifact(self, name: str, content: str, artifact_type: str = "implementation_plan") -> str:
        """Mock method for creating an artifact that will be synced to the UI."""
        self.artifacts[name] = {
            "type": artifact_type,
            "content": content,
            "status": "pending_review"
        }
        logger.info(f"Architect generated artifact: {name} ({artifact_type})")
        return f"Artifact '{name}' created for review on the Digital Whiteboard."

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Executes the coding agent workflow."""
        logger.info(f"Architect processing request: {input}")

        # In a real run, this would be an LLM loop creating artifacts.
        # For our mock/implementation setup, we simulate the artifact creation.
        if "plan" in input.lower() or "build" in input.lower():
            plan_content = "# Implementation Plan\\n\\n1. Research\\n2. Build\\n3. Verify"
            msg = self.create_artifact("feature_plan.md", plan_content, "implementation_plan")
            return AgentResult(content=f"I have created the plan on the Digital Whiteboard. {msg}")

        elif "walkthrough" in input.lower() or "done" in input.lower():
            wt_content = "# Walkthrough\\n\\nHere is what I built..."
            msg = self.create_artifact("feature_walkthrough.md", wt_content, "walkthrough")
            return AgentResult(content=f"Coding complete. {msg}")

        else:
            return AgentResult(content="Architect is standing by. What are we building today?")

