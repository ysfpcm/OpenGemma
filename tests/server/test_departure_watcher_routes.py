from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.context import ContextStore
from openjarvis.departure import DepartureWatcherService
from openjarvis.server.departure_watcher_routes import router


def test_watcher_list_returns_durable_reports_not_the_frontend_fallback(
    tmp_path,
) -> None:
    store = ContextStore(tmp_path / "context.db")
    service = DepartureWatcherService(
        store,
        home_assistant_bridge=SimpleNamespace(is_configured=True),
        monitor_available=True,
    )
    service.create_watcher(conversation_id="route-test", watcher_id="route-watcher")
    app = FastAPI()
    app.state.departure_watcher_service = service
    app.include_router(router)

    response = TestClient(app).get("/v1/departure-watchers")

    assert response.status_code == 200
    assert response.json()[0]["watcher_id"] == "route-watcher"
    assert response.json()[0]["current_status"] == "ACTIVE"
    store.close()
