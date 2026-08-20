from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server.situation_routes import configure_phase4, router
from openjarvis.situations import DurableEventJournal, ShadowSituationDetector

BASE = datetime(2026, 8, 17, 7, 0, tzinfo=timezone.utc)


def _payload(event_type: str, minute: int) -> dict:
    if event_type == "calendar.appointment":
        return {
            "appointment_id": "route-appointment",
            "route_id": "home-office",
            "starts_at": (BASE + timedelta(minutes=105)).isoformat(),
            "prep_minutes": 30,
            "status": "confirmed",
        }
    if event_type == "traffic.estimate":
        return {"route_id": "home-office", "travel_minutes": 20, "buffer_minutes": 10}
    return {"person": "Marc", "location": "home", "present": True}


def test_situation_api_persists_before_shadow_evaluation_and_is_read_only(tmp_path):
    journal = DurableEventJournal(tmp_path / "route.db")
    detector = ShadowSituationDetector(journal)
    app = FastAPI()
    app.state.phase4_journal = journal
    app.state.phase4_detector = detector
    app.include_router(router)
    client = TestClient(app)

    responses = []
    for event_type, minute in (
        ("calendar.appointment", 0),
        ("traffic.estimate", 40),
        ("presence.home", 46),
    ):
        response = client.post(
            "/v1/situations/events",
            json={
                "event_type": event_type,
                "source": "route-fixture",
                "source_event_id": f"{event_type}:{minute}",
                "observed_at": (BASE + timedelta(minutes=minute)).isoformat(),
                "payload": _payload(event_type, minute),
            },
        )
        assert response.status_code == 200
        responses.append(response.json())

    assert responses[0]["inserted"] is True
    assert responses[-1]["situations"][0]["situation_type"] == "Departure"
    assert responses[-1]["side_effects"] is False

    duplicate = client.post(
        "/v1/situations/events",
        json={
            "event_type": "presence.home",
            "source": "route-fixture",
            "source_event_id": "presence.home:46",
            "observed_at": (BASE + timedelta(minutes=46)).isoformat(),
            "payload": _payload("presence.home", 46),
        },
    )
    assert duplicate.json()["inserted"] is False

    view = client.get("/v1/situations")
    assert view.status_code == 200
    assert view.json()["mode"] == "shadow"
    assert len(view.json()["situations"]) == 1
    assert view.json()["dead_letters"] == []

    journal.close()


def test_situation_api_rejects_malformed_event_without_persisting(tmp_path):
    journal = DurableEventJournal(tmp_path / "malformed-route.db")
    app = FastAPI()
    app.state.phase4_journal = journal
    app.state.phase4_detector = ShadowSituationDetector(journal)
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/v1/situations/events",
        json={
            "event_type": "calendar.appointment",
            "source": "route-fixture",
            "observed_at": "not-a-timestamp",
            "payload": {},
        },
    )
    assert response.status_code == 422
    assert journal.events() == []
    journal.close()


def test_situation_api_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OPHANIM_PHASE_4_ENABLED", raising=False)
    app = FastAPI()
    configure_phase4(app)
    app.include_router(router)
    response = TestClient(app).get("/v1/situations")
    assert response.status_code == 503
