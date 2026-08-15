"""Read-only Codex mission API tests."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.codex_observer.store import CodexMissionStore
from openjarvis.server.codex_routes import router


class StubSupervisor:
    def __init__(self, store: CodexMissionStore) -> None:
        self.store = store
        self.capabilities = {"mode": "read-only-observer", "protocol": "v2"}


def test_codex_api_exposes_only_redacted_normalized_history(tmp_path: Path) -> None:
    store = CodexMissionStore(tmp_path / "observer.db")
    mission_id = store.create_mission("Inspect", str(tmp_path))
    app = FastAPI()
    app.state.codex_observer = StubSupervisor(store)
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/v1/codex/capabilities").json()["mode"] == "read-only-observer"
    listing = client.get("/v1/codex/missions").json()
    assert listing["mode"] == "read-only"
    assert listing["missions"][0]["id"] == mission_id
    detail = client.get(f"/v1/codex/missions/{mission_id}")
    assert detail.status_code == 200
    assert "raw_events" not in detail.json()
    assert client.get("/v1/codex/missions/missing").status_code == 404
    store.close()
