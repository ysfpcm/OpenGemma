"""Contract tests for the opt-in Phase 5 inspection API."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server.world_routes import configure_phase5, router
from openjarvis.world_model import WorldModelStore


def test_world_routes_explain_and_reconstruct_are_read_only(tmp_path):
    store = WorldModelStore(tmp_path / "routes.db")
    entity = store.ensure_entity("person", "Marc")
    store.add_observation(
        source_id="route-fixture",
        source_type="fixture",
        entity_kind="person",
        entity_name="Marc",
        predicate="priority",
        value="Project A",
        confidence=0.8,
        observed_at="2026-08-01T00:00:00Z",
        recorded_at="2026-08-01T00:00:00Z",
        evidence_text="Project A is the priority",
    )
    app = FastAPI()
    app.state.phase5_store = store
    app.include_router(router)
    client = TestClient(app)
    try:
        response = client.get(
            "/v1/world/beliefs/explain",
            params={"entity_id": entity.entity_id, "predicate": "priority"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["explanation"]["current"]["value"] == "Project A"
        assert body["explanation"]["current"]["supporting_evidence_ids"]
        assert body["side_effects"] is False

        reconstructed = client.get(
            "/v1/world/reconstruct", params={"as_of": "2026-08-02T00:00:00Z"}
        )
        assert reconstructed.status_code == 200
        assert reconstructed.json()["beliefs"][0]["value"] == "Project A"

        listed = client.get("/v1/world/beliefs", params={"entity_id": entity.entity_id})
        assert listed.status_code == 200
        assert listed.json()["side_effects"] is False
    finally:
        store.close()


def test_world_routes_reject_malformed_observation_before_any_write(tmp_path):
    store = WorldModelStore(tmp_path / "invalid-routes.db")
    app = FastAPI()
    app.state.phase5_store = store
    app.include_router(router)
    client = TestClient(app)
    base = {
        "source_id": "route-fixture",
        "entity_name": "Marc",
        "predicate": "priority",
        "value": "Project A",
    }
    try:
        for field, value in (
            ("entity_kind", "not-a-kind"),
            ("information_kind", "not-an-information-kind"),
            ("claim_class", "not-a-claim-class"),
            ("confidence", 2.0),
            ("observed_at", "not-a-timestamp"),
        ):
            response = client.post(
                "/v1/world/observations", json={**base, field: value}
            )
            assert response.status_code == 422
        assert store.sources() == []
        assert store.entities() == []
        assert store.observations() == []
    finally:
        store.close()


def test_world_routes_ingest_is_typed_and_duplicate_safe(tmp_path):
    store = WorldModelStore(tmp_path / "ingest-route.db")
    app = FastAPI()
    app.state.phase5_store = store
    app.include_router(router)
    client = TestClient(app)
    payload = {
        "source_id": "route-fixture",
        "entity_name": "Marc",
        "predicate": "priority",
        "value": "Project A",
        "observed_at": "2026-08-01T00:00:00Z",
    }
    try:
        first = client.post("/v1/world/observations", json=payload)
        second = client.post("/v1/world/observations", json=payload)
        assert first.status_code == 200
        assert second.status_code == 200
        assert (
            first.json()["observation"]["observation_id"]
            == second.json()["observation"]["observation_id"]
        )
        assert second.json()["side_effects"] is False
        assert len(store.observations()) == 1
    finally:
        store.close()


def test_world_routes_are_disabled_without_opt_in():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.get("/v1/world/beliefs")
    assert response.status_code == 503


def test_phase5_configuration_is_opt_in_and_uses_isolated_db(tmp_path, monkeypatch):
    disabled_app = FastAPI()
    monkeypatch.setenv("OPHANIM_PHASE_5_ENABLED", "0")
    configure_phase5(disabled_app)
    assert disabled_app.state.phase5_store is None

    db_path = tmp_path / "configured-world.db"
    enabled_app = FastAPI()
    monkeypatch.setenv("OPHANIM_PHASE_5_ENABLED", "1")
    monkeypatch.setenv("OPHANIM_PHASE5_DB", str(db_path))
    configure_phase5(enabled_app)
    try:
        assert enabled_app.state.phase5_store is not None
        assert db_path.exists()
    finally:
        enabled_app.state.phase5_store.close()
