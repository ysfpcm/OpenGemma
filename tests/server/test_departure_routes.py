from __future__ import annotations

from contextlib import suppress
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.cognition import ActionLedger
from openjarvis.departure import (
    DepartureGuardian,
    DeparturePolicy,
    DepartureStore,
    FakeHomeAssistantAdapter,
    FakeNotificationAdapter,
    Phase7Flags,
    register_departure_adapters,
)
from openjarvis.guardian import ActionRegistry, GuardianKernel
from openjarvis.planning import Phase6Controller, PlanningStore
from openjarvis.server.departure_routes import router
from tests.acceptance.phase_7.test_departure_guardian_pilot import _observations, _setup


@pytest.fixture
def route_harness(tmp_path):
    home = FakeHomeAssistantAdapter(
        {"light.downstairs": {"entity_id": "light.downstairs", "state": "on"}}
    )
    notifications = FakeNotificationAdapter()
    registry = ActionRegistry()
    register_departure_adapters(registry, home, notifications)
    ledger = ActionLedger(tmp_path / "guardian.db")
    guardian = GuardianKernel(ledger, registry)
    planning = Phase6Controller(guardian, PlanningStore(tmp_path / "planning.db"))
    service = DepartureGuardian(
        guardian,
        planning,
        DepartureStore(tmp_path / "departure.db"),
        flags=Phase7Flags(enabled=True, delayed_execution_enabled=True),
    )
    yield service
    with suppress(Exception):
        service.close()
    with suppress(Exception):
        planning.close()
    with suppress(Exception):
        guardian.close()
    with suppress(Exception):
        ledger.close()


def test_departure_api_is_disabled_without_explicit_service():
    app = FastAPI()
    app.state.phase7_departure = None
    app.include_router(router)
    response = TestClient(app).get("/v1/departure/setups/missing")
    assert response.status_code == 503


def test_departure_status_reports_live_standby_without_side_effects():
    app = FastAPI()
    app.state.phase7_departure = SimpleNamespace(
        flags=Phase7Flags(enabled=True, delayed_execution_enabled=False)
    )
    app.state.phase7_adapter_mode = "live-standby"
    app.include_router(router)

    response = TestClient(app).get("/v1/departure/status")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "adapter_mode": "live-standby",
        "standby": True,
        "delayed_execution_enabled": False,
        "side_effects": False,
    }


def test_departure_api_exposes_setup_recalculation_and_inspection(route_harness):
    service = route_harness
    app = FastAPI()
    app.state.phase7_departure = service
    app.include_router(router)
    client = TestClient(app)
    setup = _setup(policy=DeparturePolicy(execution_enabled=True))
    saved = client.post("/v1/departure/setups", json={"setup": setup.to_dict()})
    assert saved.status_code == 200
    assert saved.json()["side_effects"] is False

    observations = _observations()
    recalculated = client.post(
        "/v1/departure/setups/departure-home/recalculate",
        json={"observations": observations.to_dict()},
    )
    assert recalculated.status_code == 200
    assert recalculated.json()["result"]["plan_version"] == 1

    inspected = client.get("/v1/departure/departure-1")
    assert inspected.status_code == 200
    assert inspected.json()["side_effects"] is False
    assert inspected.json()["plan"]["plan"]["situation_type"] == "Departure"


def test_departure_run_api_discloses_when_execution_is_enabled(route_harness):
    service = route_harness
    app = FastAPI()
    app.state.phase7_departure = service
    app.include_router(router)
    client = TestClient(app)
    setup = _setup(policy=DeparturePolicy(execution_enabled=True))
    assert (
        client.post("/v1/departure/setups", json={"setup": setup.to_dict()}).status_code
        == 200
    )
    observations = _observations()
    assert (
        client.post(
            "/v1/departure/setups/departure-home/recalculate",
            json={"observations": observations.to_dict()},
        ).status_code
        == 200
    )
    approval = client.post(
        "/v1/departure/departure-1/approve",
        json={
            "step_id": "action-0",
            "observations": observations.to_dict(),
        },
    )
    assert approval.status_code == 200
    scheduled = client.post(
        "/v1/departure/departure-1/schedule",
        json={
            "approval_ids": {
                "action-0": "approval:departure-plan-departure-1:1:action-0"
            }
        },
    )
    assert scheduled.status_code == 200
    run = client.post(
        "/v1/departure/departure-1/run",
        json={
            "observations": {
                **observations.to_dict(),
                "actual_departure_detected": True,
            }
        },
    )

    assert run.status_code == 200
    assert run.json()["side_effects"] is True
    assert run.json()["schedules"][0]["status"] == "completed"
