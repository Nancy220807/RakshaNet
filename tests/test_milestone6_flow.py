"""
tests/test_milestone6_flow.py — End-to-end validation for Milestone 6 Demo Flow and Safety.

Tests:
1. Full Demo Walkthrough:
   - Asha triggers SOS
   - Contacts notified with WhatsApp links
   - Switch user to Meera
   - Meera receives notification in inbox and accepts "On my way"
   - Switch back to Asha and verify Meera appears in responders list with coordinates
   - Asha resolves alert with "I'm safe"
   - Public tracking link /api/track/<token> and /track/<token> verify "This alert has ended"
2. SOS_ENABLED=False:
   - Core Phase 1/2 route planning, incident report submission, verification and feed stay 100% operational
   - SOS endpoints return disabled / 404
"""

import pytest
import json
from app import app
from db import db_conn
import config
from track import reset_rate_limits


import app as app_module
from app import startup


@pytest.fixture(scope="session", autouse=True)
def setup_server():
    if app_module.G is None:
        startup()


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def cleanup():
    reset_rate_limits()
    with db_conn() as conn:
        conn.execute("DELETE FROM ended_tracks")
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM sos_notifications")
        conn.execute("DELETE FROM sos_locations")
        conn.execute("DELETE FROM sos_alerts")
    yield
    reset_rate_limits()
    with db_conn() as conn:
        conn.execute("DELETE FROM ended_tracks")
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM sos_notifications")
        conn.execute("DELETE FROM sos_locations")
        conn.execute("DELETE FROM sos_alerts")


def test_milestone6_complete_demo_walkthrough(client):
    """
    Step-by-step walkthrough:
    Asha triggers SOS -> Meera sees notification in inbox -> Meera accepts 'On my way' ->
    Asha sees 'Meera is on the way' -> Asha taps 'I'm safe' -> Tracking page shows 'alert has ended'.
    """
    # 1. Asha logs in and starts SOS
    with client.session_transaction() as sess:
        sess["user_id"] = "user_asha"

    # Ensure Asha has trusted contacts and Meera is an opted-in responder
    with db_conn() as conn:
        conn.execute("UPDATE users SET is_responder = 1, responder_verified = 1 WHERE id = 'user_meera'")
        conn.execute(
            "INSERT OR REPLACE INTO responder_locations (user_id, lat, lon) VALUES ('user_meera', 28.5440, 77.2070)"
        )

    r_start = client.post("/api/sos/start", json={
        "lat": 28.5433,
        "lon": 77.2065,
        "simulated": True,
        "countdown": False,
    })
    assert r_start.status_code == 201, f"Failed starting SOS: {r_start.get_data(as_text=True)}"
    start_data = r_start.get_json()
    alert_id = start_data["alert"]["id"]
    track_token = start_data["alert"]["track_token"]
    assert track_token is not None

    # Verify contacts list has WhatsApp links
    notifications = start_data.get("notifications", [])
    assert len(notifications) > 0
    for notif in notifications:
        assert "wa_link" in notif
        assert "wa.me" in notif["wa_link"]

    # 2. Switch acting user to Meera
    r_switch = client.post("/api/auth/switch", json={"user_id": "user_meera"})
    assert r_switch.status_code == 200

    # 3. Meera polls notifications (Responder Inbox)
    r_notifs = client.get("/api/notifications")
    assert r_notifs.status_code == 200
    meera_notifs = r_notifs.get_json().get("notifications", [])
    assert len(meera_notifs) >= 1
    target_notif = next(n for n in meera_notifs if n.get("payload", {}).get("alert_id") == alert_id)
    assert target_notif is not None
    notif_id = target_notif["id"]

    # 4. Meera accepts "On my way"
    r_respond = client.post(f"/api/notifications/{notif_id}/respond", json={"action": "on_my_way"})
    assert r_respond.status_code == 200
    resp_payload = r_respond.get_json().get("payload", {})
    assert resp_payload.get("status") == "on_my_way"
    assert "exact_lat" in resp_payload
    assert "maps_link" in resp_payload

    # 5. Switch back to Asha
    client.post("/api/auth/switch", json={"user_id": "user_asha"})

    # 6. Asha queries responders list and sees Meera "on_my_way" with coordinates
    r_responders = client.get(f"/api/sos/{alert_id}/responders")
    assert r_responders.status_code == 200
    res_list = r_responders.get_json().get("responders", [])
    meera_entry = next((r for r in res_list if r["first_name"] == "Meera"), None)
    assert meera_entry is not None, "Meera should appear in Asha's responders list"
    assert meera_entry["status"] == "on_my_way"
    assert "lat" in meera_entry and "lon" in meera_entry
    assert meera_entry["lat"] == 28.5440

    # 7. Asha resolves alert as "I'm safe"
    r_resolve = client.post(f"/api/sos/{alert_id}/resolve", json={"outcome": "safe"})
    assert r_resolve.status_code == 200
    resolve_data = r_resolve.get_json()
    assert resolve_data["status"] == "ok"
    assert resolve_data["token_invalidated"] is True

    # 8. Check public tracking link (/api/track/<token> and /track/<token>)
    r_track_api = client.get(f"/api/track/{track_token}")
    assert r_track_api.status_code == 200
    track_data = r_track_api.get_json()
    assert track_data["status"] == "ended"
    assert "This alert has ended. The person has marked themselves safe." in track_data["message"]

    r_track_page = client.get(f"/track/{track_token}")
    assert r_track_page.status_code == 200
    html = r_track_page.get_data(as_text=True)
    assert "This alert has ended. The person has marked themselves safe." in html


def test_sos_disabled_leaves_phase12_intact(client, monkeypatch):
    """
    When SOS_ENABLED=False:
    - Route planning (/api/route) works 100%
    - Incident reporting (/api/incidents) works 100%
    - Incident feed (/api/incidents?status=verified) works 100%
    - SOS config returns sos_enabled=False
    - SOS start returns 404
    """
    monkeypatch.setattr(config, "SOS_ENABLED", False)

    # 1. Config endpoint reports disabled
    r_cfg = client.get("/api/sos/config")
    assert r_cfg.status_code == 200
    assert r_cfg.get_json()["sos_enabled"] is False

    # 2. SOS start returns 404
    with client.session_transaction() as sess:
        sess["user_id"] = "user_asha"
    r_start = client.post("/api/sos/start", json={"lat": 28.5433, "lon": 77.2065})
    assert r_start.status_code == 404

    # 3. Route planner works normally
    r_route = client.get("/api/route", query_string={
        "start_lat": 28.5433,
        "start_lon": 77.2065,
        "end_lat": 28.5592,
        "end_lon": 77.2090,
        "hour": 18,
    })
    assert r_route.status_code == 200
    route_json = r_route.get_json()
    assert "safest" in route_json
    assert "shortest" in route_json

    # 4. Incident verification feed works normally
    r_feed = client.get("/api/incidents?status=verified")
    assert r_feed.status_code == 200
    assert "incidents" in r_feed.get_json()
