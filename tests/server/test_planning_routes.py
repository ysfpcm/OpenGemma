"""Phase 6 API exposes review state without adding an execution sink."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server.planning_routes import configure_phase6, router
from tests.acceptance.phase_6.test_structured_planning_contextual_authorization import (
    _context,
    _controller,
    _departure_plan,
    _grant,
)


def test_planning_api_inspects_edits_grants_and_authorization(tmp_path):
    controller, guardian, ledger = _controller(tmp_path)
    context = _context()
    original = _departure_plan(controller, context)
    app = FastAPI()
    app.state.phase6_controller = controller
    app.include_router(router)
    client = TestClient(app)

    inspected = client.get(f"/v1/planning/plans/{original.plan_id}")
    assert inspected.status_code == 200
    assert inspected.json()["side_effects"] is False
    assert len(inspected.json()["versions"]) == 1

    edited = client.post(
        f"/v1/planning/plans/{original.plan_id}/edits",
        json={
            "remove_step_ids": ["light"],
            "edit_id": "api-remove-light",
            "reason": "Marc removed the light action",
            "context": context.to_dict(),
        },
    )
    assert edited.status_code == 200
    assert edited.json()["plan"]["version"] == 2
    assert "light" in edited.json()["plan"]["removed_step_ids"]

    grant = _grant(controller.store.get_plan(original.plan_id))
    assert (
        client.post(
            "/v1/planning/policies/fixture.thermostat.away", json={"level": 3}
        ).status_code
        == 200
    )
    granted = client.post("/v1/planning/grants", json={"grant": grant.to_dict()})
    assert granted.status_code == 200
    decision = client.post(
        f"/v1/planning/plans/{original.plan_id}/authorize",
        json={
            "plan_version": 2,
            "step_id": "thermostat",
            "context": context.to_dict(),
            "grant_id": grant.grant_id,
        },
    )
    assert decision.status_code == 200
    assert decision.json()["decision"]["allowed"] is True
    assert decision.json()["side_effects"] is False

    versions = client.get(f"/v1/planning/plans/{original.plan_id}/versions")
    assert versions.status_code == 200
    assert len(versions.json()["versions"]) == 2
    controller.close()
    guardian.close()
    ledger.close()


def test_planning_api_is_disabled_without_controller():
    app = FastAPI()
    app.state.phase6_controller = None
    app.include_router(router)
    client = TestClient(app)
    response = client.get("/v1/planning/plans/missing")
    assert response.status_code == 503


def test_phase6_configuration_is_disabled_by_default(monkeypatch, tmp_path):
    db_path = tmp_path / "should-not-be-created.db"
    monkeypatch.delenv("OPHANIM_PHASE_6_ENABLED", raising=False)
    monkeypatch.setenv("OPHANIM_PHASE6_DB", str(db_path))
    app = FastAPI()

    configure_phase6(app)

    assert app.state.phase6_store is None
    assert app.state.phase6_controller is None
    assert not db_path.exists()
