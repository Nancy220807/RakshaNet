"""
tests/test_sos_core.py — Phase 3 Milestone 3: Test Suite for SOS Core Engine.

Tests:
1. SOS start creates an alert and simulated notifications
2. Cooldown enforced
3. Daily limit enforced
4. No-contacts error
5. Outside-the-area error
6. Only the owner can resolve or cancel
7. Cancel window enforced
8. Resolve invalidates the token
9. The location rate limit works
10. SOS_ENABLED=False returns 404 for the SOS endpoints
"""

import time
from datetime import datetime, timedelta
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
def clean_sos_tables():
    """Wipe any test SOS data after each test so row counts remain exact."""
    yield
    with db_conn() as conn:
        conn.execute("DELETE FROM ended_tracks")
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM sos_notifications")
        conn.execute("DELETE FROM sos_locations")
        conn.execute("DELETE FROM sos_alerts")
        conn.execute("DELETE FROM trusted_contacts WHERE user_id LIKE 'user_tsos_%'")
        conn.execute("DELETE FROM users WHERE id LIKE 'user_tsos_%'")


def create_test_user(user_id="user_tsos_asha", name="Test Asha", with_contacts=True):
    """Helper to create a test user with optional trusted contacts."""
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, name, email, phone) VALUES (?, ?, ?, ?)",
            (user_id, name, f"{user_id}@test.com", "+919876543210"),
        )
        if with_contacts:
            conn.execute("DELETE FROM trusted_contacts WHERE user_id = ?", (user_id,))
            conn.execute(
                """
                INSERT INTO trusted_contacts (user_id, name, phone, relation)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, "Sister Riya", "+919876543211", "Sister"),
            )
            conn.execute(
                """
                INSERT INTO trusted_contacts (user_id, name, phone, relation)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, "Friend Meera", "+919876543212", "Friend"),
            )


# ── Test 1: Start creates alert and simulated notifications ──────────────────

def test_sos_start_creates_alert_and_notifications(client):
    """Test 1: SOS start creates an alert and simulated notifications."""
    uid = "user_tsos_1"
    create_test_user(uid, "Asha Test", with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    res = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert res.status_code == 201
    data = res.get_json()
    assert data["status"] == "ok"
    alert = data["alert"]
    assert alert["status"] == "active"
    assert alert["track_token"] is not None
    assert alert["notifications_count"] == 2
    for n in alert["notifications"]:
        assert n["status"] == "simulated"
        assert "wa_link" in n

    # Verify DB records
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM sos_alerts WHERE id = ?", (alert["id"],)).fetchone()
        assert row is not None
        assert row["status"] == "active"
        assert row["user_id"] == uid

        loc = conn.execute("SELECT * FROM sos_locations WHERE alert_id = ?", (alert["id"],)).fetchone()
        assert loc is not None
        assert pytest.approx(loc["lat"], rel=1e-3) == 28.5520

        notifs = conn.execute(
            "SELECT * FROM sos_notifications WHERE alert_id = ? AND recipient_type = 'contact'",
            (alert["id"],)
        ).fetchall()
        assert len(notifs) == 2


# ── Test 2: Cooldown enforced ────────────────────────────────────────────────

def test_sos_cooldown_enforced(client, monkeypatch):
    """Test 2: Cooldown enforced between consecutive SOS alerts."""
    monkeypatch.setattr(config, "SOS_COOLDOWN_SECONDS", 30)
    uid = "user_tsos_cool"
    create_test_user(uid, "Cool User", with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    # First alert succeeds
    r1 = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r1.status_code == 201
    aid1 = r1.get_json()["alert"]["id"]

    # Resolve first alert so user doesn't hit single-active-alert check
    r_res = client.post(f"/api/sos/{aid1}/resolve", json={"outcome": "safe"})
    assert r_res.status_code == 200

    # Second immediate alert within 30s is rejected with 429
    r2 = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r2.status_code == 429
    assert "cooldown" in r2.get_json()["error"].lower()


# ── Test 3: Daily limit enforced ─────────────────────────────────────────────

def test_sos_daily_limit_enforced(client, monkeypatch):
    """Test 3: Daily limit (SOS_MAX_PER_DAY) enforced."""
    monkeypatch.setattr(config, "SOS_MAX_PER_DAY", 3)
    monkeypatch.setattr(config, "SOS_COOLDOWN_SECONDS", 0)  # bypass cooldown for test
    uid = "user_tsos_daily"
    create_test_user(uid, "Daily User", with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    # Seed 3 resolved alerts within the last hour
    with db_conn() as conn:
        for i in range(3):
            conn.execute(
                """
                INSERT INTO sos_alerts (user_id, status, start_lat, start_lon, track_token, created_at)
                VALUES (?, 'resolved', 28.5520, 77.2040, ?, datetime('now', '-30 minutes'))
                """,
                (uid, f"token_daily_{i}"),
            )

    # 4th alert exceeds max 3 per day -> 429
    r4 = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r4.status_code == 429
    assert "daily" in r4.get_json()["error"].lower()


# ── Test 4: No-contacts error ────────────────────────────────────────────────

def test_sos_no_contacts_error(client):
    """Test 4: Error if user has no trusted contacts."""
    uid = "user_tsos_nocontacts"
    create_test_user(uid, "Lonely User", with_contacts=False)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    r = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r.status_code == 400
    assert "no trusted contacts" in r.get_json()["error"].lower()


# ── Test 5: Outside-the-area error ───────────────────────────────────────────

def test_sos_outside_area_error(client):
    """Test 5: Error if coordinates are outside coverage area (without simulated flag)."""
    uid = "user_tsos_out"
    create_test_user(uid, "Outside User", with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    # Far away in Mumbai (19.07, 72.87) while coverage is Hauz Khas / Delhi
    r = client.post("/api/sos/start", json={"lat": 19.0760, "lon": 72.8777})
    assert r.status_code == 400
    assert "outside" in r.get_json()["error"].lower()


# ── Test 6: Only owner can resolve or cancel ─────────────────────────────────

def test_only_owner_can_resolve_or_cancel(client):
    """Test 6: Only the alert owner can resolve or cancel."""
    owner = "user_tsos_owner"
    other = "user_tsos_intruder"
    create_test_user(owner, "Alert Owner", with_contacts=True)
    create_test_user(other, "Intruder User", with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = owner

    r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    assert r_start.status_code == 201
    aid = r_start.get_json()["alert"]["id"]

    # Switch session to intruder
    with client.session_transaction() as sess:
        sess["user_id"] = other

    # Intruder tries to resolve -> 403
    r_bad_res = client.post(f"/api/sos/{aid}/resolve", json={"outcome": "safe"})
    assert r_bad_res.status_code == 403

    # Intruder tries to cancel -> 403
    r_bad_cancel = client.post(f"/api/sos/{aid}/cancel")
    assert r_bad_cancel.status_code == 403

    # Switch back to owner -> success
    with client.session_transaction() as sess:
        sess["user_id"] = owner

    r_ok_res = client.post(f"/api/sos/{aid}/resolve", json={"outcome": "safe"})
    assert r_ok_res.status_code == 200


# ── Test 7: Cancel window enforced ───────────────────────────────────────────

def test_sos_cancel_window_enforced(client, monkeypatch):
    """Test 7: Cancel allowed only within SOS_COUNTDOWN_SECONDS + 10s of creation."""
    monkeypatch.setattr(config, "SOS_COUNTDOWN_SECONDS", 5)
    uid = "user_tsos_cancel"
    create_test_user(uid, "Cancel User", with_contacts=True)

    # Alert created 20 seconds ago (> 5 + 10 = 15s window)
    with db_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO sos_alerts (user_id, status, start_lat, start_lon, track_token, created_at)
            VALUES (?, 'active', 28.5520, 77.2040, 'tok_cancel_test', datetime('now', '-20 seconds'))
            """,
            (uid,),
        )
        aid = cur.lastrowid

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    r_cancel = client.post(f"/api/sos/{aid}/cancel")
    assert r_cancel.status_code == 400
    assert "expired" in r_cancel.get_json()["error"].lower()


# ── Test 8: Resolve invalidates the token ────────────────────────────────────

def test_sos_resolve_invalidates_token(client):
    """Test 8: Resolve invalidates the tracking token."""
    uid = "user_tsos_res"
    create_test_user(uid, "Resolve User", with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    aid = r_start.get_json()["alert"]["id"]
    token = r_start.get_json()["alert"]["track_token"]
    assert token is not None

    r_res = client.post(f"/api/sos/{aid}/resolve", json={"outcome": "safe"})
    assert r_res.status_code == 200

    # Token must be cleared in DB
    with db_conn() as conn:
        row = conn.execute("SELECT track_token, status, resolved_at FROM sos_alerts WHERE id = ?", (aid,)).fetchone()
        assert row["status"] == "resolved"
        assert row["track_token"] is None
        assert row["resolved_at"] is not None


# ── Test 9: Location rate limit works ────────────────────────────────────────

def test_sos_location_rate_limit(client):
    """Test 9: Location update rate limit (1 per second) works."""
    uid = "user_tsos_loc"
    create_test_user(uid, "Loc User", with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
    aid = r_start.get_json()["alert"]["id"]

    # First location update succeeds
    r_loc1 = client.post(f"/api/sos/{aid}/location", json={"lat": 28.5522, "lon": 77.2042})
    assert r_loc1.status_code == 200

    # Immediate second location update (< 1s) returns 429
    r_loc2 = client.post(f"/api/sos/{aid}/location", json={"lat": 28.5524, "lon": 77.2044})
    assert r_loc2.status_code == 429
    assert "rate limit" in r_loc2.get_json()["error"].lower()


# ── Test 10: SOS_ENABLED=False returns 404 ───────────────────────────────────

def test_sos_disabled_returns_404(client):
    """Test 10: When SOS_ENABLED=False, all SOS endpoints return 404."""
    uid = "user_tsos_disabled"
    create_test_user(uid, "Disabled User", with_contacts=True)

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    with patch("config.SOS_ENABLED", False):
        r_start = client.post("/api/sos/start", json={"lat": 28.5520, "lon": 77.2040})
        assert r_start.status_code == 404

        r_active = client.get("/api/sos/active")
        assert r_active.status_code == 404

        r_loc = client.post("/api/sos/1/location", json={"lat": 28.5520, "lon": 77.2040})
        assert r_loc.status_code == 404

        r_res = client.post("/api/sos/1/resolve", json={"outcome": "safe"})
        assert r_res.status_code == 404


def test_sos_config_endpoint(client):
    """Test 11: GET /api/sos/config returns expected configuration values."""
    res = client.get("/api/sos/config")
    assert res.status_code == 200
    data = res.get_json()
    assert "sos_enabled" in data
    assert "demo_mode" in data
    assert data["countdown_seconds"] == 5
    assert data["location_update_seconds"] == 5
    assert data["responder_radius_m"] == 800
    assert data["max_contacts"] == 3

