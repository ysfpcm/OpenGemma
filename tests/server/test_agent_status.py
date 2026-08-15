"""Tests for the operator-facing managed-agent status endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.agents.manager import AgentManager
from openjarvis.server.agent_manager_routes import create_agent_manager_router


class _Scheduler:
    def get_agent_schedule(self, agent_id: str) -> dict:
        return {
            "schedule_type": "cron",
            "schedule_value": "0 5 * * *",
            "next_run_at": 200.0,
        }


def test_status_endpoint_exposes_run_schedule_tool_and_delivery(tmp_path):
    manager = AgentManager(str(tmp_path / "agents.db"))
    agent = manager.create_agent(
        "Commute Readiness",
        config={"schedule_type": "cron", "schedule_value": "0 5 * * *"},
    )
    manager.store_agent_response(
        agent["id"],
        "The traffic lookup succeeded, but the notification failed.",
        tool_calls=[
            {
                "tool": "channel_send",
                "success": False,
                "result": "No channel backend configured.",
                "latency": 0.01,
            }
        ],
    )

    app = FastAPI()
    agents_router, *_ = create_agent_manager_router(manager)
    app.include_router(agents_router)
    app.state.agent_scheduler = _Scheduler()

    response = TestClient(app).get("/v1/managed-agents/status")

    assert response.status_code == 200
    status = response.json()["agents"][0]
    assert status["next_run_at"] == 200.0
    assert status["last_tool_result"]["tool"] == "channel_send"
    assert status["delivery_result"]["status"] == "failed"
    assert status["overall_status"] == "action_failed"
    assert "No channel backend" in status["failure_reason"]
    manager.close()


def test_status_endpoint_marks_non_delivery_tool_failure(tmp_path):
    manager = AgentManager(str(tmp_path / "agents.db"))
    agent = manager.create_agent("Memory Agent")
    manager.store_agent_response(
        agent["id"],
        "Memory lookup failed.",
        tool_calls=[
            {
                "tool": "memory_retrieve",
                "success": False,
                "result": "No memory backend configured.",
                "latency": 0.01,
            }
        ],
    )

    app = FastAPI()
    agents_router, *_ = create_agent_manager_router(manager)
    app.include_router(agents_router)

    response = TestClient(app).get("/v1/managed-agents/status")

    assert response.status_code == 200
    status = response.json()["agents"][0]
    assert status["overall_status"] == "action_failed"
    assert "No memory backend" in status["failure_reason"]
    manager.close()
