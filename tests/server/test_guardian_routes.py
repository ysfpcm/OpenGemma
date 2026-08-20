"""Guardian API exposes explicit authority and the complete causal chain."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.cognition import ActionLedger, ExecutionOutcome
from openjarvis.guardian import (
    ActionDefinition,
    ActionRegistry,
    GuardianKernel,
    PreconditionResult,
)
from openjarvis.server.guardian_routes import router


def test_guardian_api_executes_only_explicitly_granted_registered_action(tmp_path):
    world = {"enabled": False}
    registry = ActionRegistry()
    registry.register(
        ActionDefinition(
            action_type="fixture.enable",
            input_schema={
                "type": "object",
                "required": ["target"],
                "properties": {"target": {"type": "string"}},
                "additionalProperties": False,
            },
            capability="fixture.write",
            risk_class="reversible",
            consequence_class="low",
            preconditions=lambda _: [
                PreconditionResult("fixture", True, datetime.now(timezone.utc))
            ],
            executor=lambda _: _enable(world),
            verifier=lambda _: (world["enabled"], {"enabled": world["enabled"]}),
        )
    )
    ledger = ActionLedger(tmp_path / "guardian.db")
    kernel = GuardianKernel(ledger, registry)
    app = FastAPI()
    app.state.guardian = kernel
    app.include_router(router)
    client = TestClient(app)

    denied = client.post(
        "/v1/guardian/actions",
        json={
            "action_type": "fixture.enable",
            "parameters": {"target": "fixture"},
            "idempotency_key": "no-grant",
            "session_id": "session",
        },
    )
    assert denied.json()["allowed"] is False
    assert world["enabled"] is False

    grant = client.post(
        "/v1/guardian/grants",
        json={
            "grant_id": "grant",
            "session_id": "session",
            "capability": "fixture.write",
            "scope": {"action_type": "fixture.enable", "target": "fixture-2"},
        },
    )
    assert grant.json()["status"] == "active"
    completed = client.post(
        "/v1/guardian/actions",
        json={
            "action_type": "fixture.enable",
            "parameters": {"target": "fixture-2"},
            "idempotency_key": "with-grant",
            "session_id": "session",
        },
    )
    body = completed.json()
    assert body["state"] == "verified"
    timeline = client.get(f"/v1/guardian/actions/{body['action_id']}").json()
    assert timeline["state"] == "verified"
    assert timeline["attempts"] and timeline["verifications"]

    malformed = client.post(
        "/v1/guardian/actions",
        json={
            "action_type": "fixture.enable",
            "parameters": {"target": "fixture-2"},
            "idempotency_key": "",
            "session_id": "session",
        },
    )
    assert malformed.status_code == 422
    kernel.close()
    ledger.close()


def _enable(world):
    world["enabled"] = True
    return ExecutionOutcome(True, {"reported": "success"})
