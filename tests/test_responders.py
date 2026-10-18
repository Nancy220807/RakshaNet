"""
tests/test_responders.py — Phase 3 Milestone 4: Community Responders & Notifications Test Suite.

Tests:
1. Opt-in toggle works and stores consent timestamp & disclaimer
2. Responders are selected by radius (within RESPONDER_RADIUS_M)
3. The victim is excluded from responder notifications
4. Opted-out and unverified responders are not notified
5. The payload contains no phone number or exact coordinates before accepting
6. Only the notification owner can respond (and on_my_way reveals coordinates)
7. A responder failure does not break SOS start
8. The victim's responders list shows status updates with privacy preserved
9. SOS_ENABLED=False returns 404 for responder endpoints
"""

import json
from unittest.mock import patch
import pytest

from app import app
from db import db_conn
import config


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def clean_responder_test_data():
    """Wipe any test data created by responder tests so DB counts stay pristine."""
    yield
    with db_conn() as conn:
        conn.execute("DELETE FROM ended_tracks")
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM sos_notifications")
        conn.execute("DELETE FROM sos_locations")
        conn.execute("DELETE FROM sos_alerts")
        conn.execute("DELETE FROM trusted_contacts WHERE user_id LIKE 'user_tresp_%'")
        conn.execute("DELETE FROM responder_locations WHERE user_id LIKE 'user_tresp_%'")
        conn.execute("DELETE FROM users WHERE id LIKE 'user_tresp_%'")


def create_user(user_id, name="Test User", is_responder=0, verified=0, lat=None, lon=None, with_contacts=True):
    """Helper to create test user with location and contacts."""
    with db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO users (id, name, email, phone, is_responder, responder_verified)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, name, f"{user_id}@test.com", "+919876543210", is_responder, verified),
        )
        if lat is not None and lon is not None:
            conn.execute(
                """
                INSERT OR REPLACE INTO responder_locations (user_id, lat, lon, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (user_id, lat, lon),
            )
        if with_contacts:
            conn.execute("DELETE FROM trusted_contacts WHERE user_id = ?", (user_id,))
            conn.execute(
                """
                INSERT INTO trusted_contacts (user_id, name, phone, relation)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, "Friend Meera", "+919876543212", "Friend"),
            )


# ── Test 1: Opt-in toggle works ──────────────────────────────────────────────

def test_optin_toggle_works(client):
    """Test 1: Volunteer opt-in toggle updates is_responder, timestamp, and disclaimer."""
    uid = "user_tresp_opt"
    create_user(uid, "Optin Volunteer", is_responder=0, with_contacts=False)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    # Toggle to opt-in (1)
    r1 = client.post("/api/responders/optin", json={})
    assert r1.status_code == 200
    d1 = r1.get_json()
    assert d1["status"] == "ok"
    assert d1["is_responder"] is True
    assert "112" in d1["disclaimer"]

    with db_conn() as conn:
        u1 = conn.execute("SELECT is_responder, responder_consent_at FROM users WHERE id = ?", (uid,)).fetchone()
        assert u1["is_responder"] == 1
        assert u1["responder_consent_at"] is not None

    # Toggle to opt-out (0)
    r2 = client.post("/api/responders/optin", json={})
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert d2["is_responder"] is False

    with db_conn() as conn:
        u2 = conn.execute("SELECT is_responder FROM users WHERE id = ?", (uid,)).fetchone()
        assert u2["is_responder"] == 0


# ── Test 2: Responders are selected by radius ────────────────────────────────

def test_responders_selected_by_radius(client):
    """Test 2: Responders within RESPONDER_RADIUS_M (800m) are notified; farther ones are not."""
    victim = "user_tresp_vic"
    near_resp = "user_tresp_near"
    far_resp = "user_tresp_far"

    # Victim at Hauz Khas (28.5520, 77.2040)
    create_user(victim, "Victim User", is_responder=0, with_contacts=True)
    # Near responder ~200m away (28.5535, 77.2045) -> within 800m
    create_user(near_resp, "Near Responder", is_responder=1, verified=1, lat=28.5535, lon=77.2045, with_contacts=False)
    # Far responder ~3000m away (28.5750, 77.2200) -> outside 800m
    create_user(far_resp, "Far Responder", is_responder=1, verified=1, lat=28.5750, lon=77.2200, with_contacts=False)

    with client.session_transaction() as sess:
        sess["user_id"] = victim

    r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r_start.status_code == 201
    aid = r_start.get_json()["alert"]["id"]

    # Verify notifications table
    with db_conn() as conn:
        notif_near = conn.execute("SELECT * FROM notifications WHERE user_id = ?", (near_resp,)).fetchone()
        assert notif_near is not None
        payload = json.loads(notif_near["payload_json"])
        assert payload["alert_id"] == aid
        assert payload["approx_distance_m"] <= 800

        notif_far = conn.execute("SELECT * FROM notifications WHERE user_id = ?", (far_resp,)).fetchone()
        assert notif_far is None


# ── Test 3: The victim is excluded ───────────────────────────────────────────

def test_victim_is_excluded_from_notifications(client):
    """Test 3: If victim is also an opted-in responder, they do not notify themselves."""
    victim = "user_tresp_vic_resp"
    create_user(victim, "Victim Responder", is_responder=1, verified=1, lat=28.5520, lon=77.2040, with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = victim

    r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r_start.status_code == 201

    with db_conn() as conn:
        self_notif = conn.execute("SELECT * FROM notifications WHERE user_id = ?", (victim,)).fetchone()
        assert self_notif is None


# ── Test 4: Opted-out and unverified responders are not notified ─────────────

def test_opted_out_and_unverified_not_notified(client):
    """Test 4: Users nearby who are opted-out (is_responder=0) or unverified (responder_verified=0) are skipped."""
    victim = "user_tresp_vic4"
    opted_out = "user_tresp_optout"
    unverified = "user_tresp_unverif"

    create_user(victim, "Victim 4", is_responder=0, with_contacts=True)
    create_user(opted_out, "Opted Out", is_responder=0, verified=1, lat=28.5522, lon=77.2042, with_contacts=False)
    create_user(unverified, "Unverified", is_responder=1, verified=0, lat=28.5523, lon=77.2043, with_contacts=False)

    with client.session_transaction() as sess:
        sess["user_id"] = victim

    r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r_start.status_code == 201

    with db_conn() as conn:
        assert conn.execute("SELECT * FROM notifications WHERE user_id = ?", (opted_out,)).fetchone() is None
        assert conn.execute("SELECT * FROM notifications WHERE user_id = ?", (unverified,)).fetchone() is None


# ── Test 5: Payload contains no phone or exact coordinates before accepting ──

def test_payload_privacy_before_accepting(client):
    """Test 5: Responders see street name & approx distance, but never phone number or exact coordinates."""
    victim = "user_tresp_vic5"
    resp = "user_tresp_resp5"

    create_user(victim, "Victim 5", is_responder=0, with_contacts=True)
    create_user(resp, "Responder 5", is_responder=1, verified=1, lat=28.5525, lon=77.2045, with_contacts=False)

    with client.session_transaction() as sess:
        sess["user_id"] = victim

    r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r_start.status_code == 201

    # Switch session to responder to check their inbox
    with client.session_transaction() as sess:
        sess["user_id"] = resp

    r_inbox = client.get("/api/notifications")
    assert r_inbox.status_code == 200
    data = r_inbox.get_json()
    assert data["count"] == 1
    p = data["notifications"][0]["payload"]

    # Must contain street_name and approx_distance_m
    assert "street_name" in p
    assert "approx_distance_m" in p

    # Must NOT contain phone number or exact coordinates
    assert "phone" not in p
    assert "victim_phone" not in p
    assert "exact_lat" not in p
    assert "exact_lon" not in p
    assert "maps_link" not in p


# ── Test 6: Only notification owner can respond; on_my_way reveals coordinates ─

def test_only_owner_can_respond_and_reveals_coords(client):
    """Test 6: Only the recipient can respond. 'on_my_way' reveals exact coordinates."""
    victim = "user_tresp_vic6"
    resp = "user_tresp_resp6"
    intruder = "user_tresp_intruder6"

    create_user(victim, "Victim 6", is_responder=0, with_contacts=True)
    create_user(resp, "Responder 6", is_responder=1, verified=1, lat=28.5522, lon=77.2042, with_contacts=False)
    create_user(intruder, "Intruder 6", is_responder=1, verified=1, lat=28.5523, lon=77.2043, with_contacts=False)

    with client.session_transaction() as sess:
        sess["user_id"] = victim

    r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r_start.status_code == 201

    with db_conn() as conn:
        notif_row = conn.execute("SELECT id FROM notifications WHERE user_id = ?", (resp,)).fetchone()
        nid = notif_row["id"]

    # Intruder tries to respond -> 403
    with client.session_transaction() as sess:
        sess["user_id"] = intruder
    r_bad = client.post(f"/api/notifications/{nid}/respond", json={"action": "on_my_way"})
    assert r_bad.status_code == 403

    # Legitimate owner responds 'on_my_way' -> 200 and coordinates revealed
    with client.session_transaction() as sess:
        sess["user_id"] = resp
    r_ok = client.post(f"/api/notifications/{nid}/respond", json={"action": "on_my_way"})
    assert r_ok.status_code == 200
    res_payload = r_ok.get_json()["payload"]
    assert res_payload["status"] == "on_my_way"
    assert "exact_lat" in res_payload
    assert "exact_lon" in res_payload
    assert pytest.approx(res_payload["exact_lat"], rel=1e-3) == 28.5520
    assert "maps_link" in res_payload


# ── Test 7: A responder failure does not break SOS start ─────────────────────

def test_responder_failure_does_not_break_sos_start(client):
    """Test 7: If notify_community_responders fails or raises, SOS start still succeeds."""
    victim = "user_tresp_vic7"
    create_user(victim, "Victim 7", is_responder=0, with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = victim

    # Mock notify_community_responders to raise an exception
    with patch("responders.notify_community_responders", side_effect=RuntimeError("Simulated responder dispatch failure")):
        r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
        # Alert creation and trusted contacts notifications must still succeed
        assert r_start.status_code == 201
        data = r_start.get_json()
        assert data["status"] == "ok"
        assert data["alert"]["id"] is not None


# ── Test 8: Victim's responders list shows status updates & privacy ──────────

def test_victim_responders_list_and_privacy(client):
    """Test 8: GET /api/sos/<id>/responders shows first name, approx dist, and response status."""
    victim = "user_tresp_vic8"
    resp1 = "user_tresp_r8a"
    resp2 = "user_tresp_r8b"

    create_user(victim, "Victim 8", is_responder=0, with_contacts=True)
    create_user(resp1, "Rani Patel", is_responder=1, verified=1, lat=28.5525, lon=77.2045, with_contacts=False)
    create_user(resp2, "Dev Sharma", is_responder=1, verified=1, lat=28.5530, lon=77.2050, with_contacts=False)

    with client.session_transaction() as sess:
        sess["user_id"] = victim

    r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    aid = r_start.get_json()["alert"]["id"]

    # Resp1 accepts 'on_my_way'
    with db_conn() as conn:
        nid1 = conn.execute("SELECT id FROM notifications WHERE user_id = ?", (resp1,)).fetchone()["id"]
    with client.session_transaction() as sess:
        sess["user_id"] = resp1
    client.post(f"/api/notifications/{nid1}/respond", json={"action": "on_my_way"})

    # Resp2 declines 'cant_help'
    with db_conn() as conn:
        nid2 = conn.execute("SELECT id FROM notifications WHERE user_id = ?", (resp2,)).fetchone()["id"]
    with client.session_transaction() as sess:
        sess["user_id"] = resp2
    client.post(f"/api/notifications/{nid2}/respond", json={"action": "cant_help"})

    # Victim queries responders list
    with client.session_transaction() as sess:
        sess["user_id"] = victim

    r_list = client.get(f"/api/sos/{aid}/responders")
    assert r_list.status_code == 200
    res_data = r_list.get_json()
    assert res_data["status"] == "ok"
    assert res_data["count"] >= 2

    responders = res_data["responders"]
    statuses = {r["first_name"]: r["status"] for r in responders}
    assert statuses.get("Rani") == "on_my_way"
    assert statuses.get("Dev") == "cant_help"

    # Privacy check: NEVER expose phone number or user_id
    for r in responders:
        assert "phone" not in r
        assert "user_id" not in r
        assert r["approx_distance_m"] is not None
        if r["status"] == "on_my_way":
            assert "lat" in r and "lon" in r
            assert r["lat"] == 28.5525 and r["lon"] == 77.2045
        else:
            assert "lat" not in r
            assert "lon" not in r


# ── Test 9: SOS_ENABLED=False returns 404 ────────────────────────────────────

def test_responders_disabled_returns_404(client):
    """Test 9: When SOS_ENABLED=False, responder endpoints return 404."""
    uid = "user_tresp_dis"
    create_user(uid, "Disabled User", is_responder=1, with_contacts=False)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    with patch("config.SOS_ENABLED", False):
        assert client.post("/api/responders/optin", json={}).status_code == 404
        assert client.get("/api/notifications").status_code == 404
        assert client.post("/api/notifications/1/respond", json={"action": "on_my_way"}).status_code == 404
        assert client.get("/api/sos/1/responders").status_code == 404
