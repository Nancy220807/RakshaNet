"""
tests/test_track.py — Phase 3 Milestone 5: Test Suite for Live Tracking Page & API.

Tests:
1. Active token returns location and trail
2. Unknown token returns 404 with friendly "Link not found"
3. Resolved alert shows "ended" state ("This alert has ended. The person has marked themselves safe.")
4. Cancelled alert shows "cancelled" state ("This alert was cancelled")
5. Expired alert (>6 hours) shows "expired" state ("This link has expired")
6. Response has no PII (check keys and scan JSON for phone numbers and emails)
7. Cache-Control: no-store header is set
8. Rate limit works (60 req/min per IP, 61st returns 429)
9. SOS_ENABLED=False returns 404
"""

import re
import json
import time
from datetime import datetime, timedelta
import pytest

from app import app
from db import db_conn
import config
from track import reset_rate_limits, record_ended_track, hash_token


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def clean_track_tables():
    """Wipe any test tracking and SOS data before & after each test."""
    reset_rate_limits()
    yield
    reset_rate_limits()
    with db_conn() as conn:
        conn.execute("DELETE FROM ended_tracks")
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM sos_notifications")
        conn.execute("DELETE FROM sos_locations")
        conn.execute("DELETE FROM sos_alerts")
        conn.execute("DELETE FROM trusted_contacts WHERE user_id LIKE 'user_ttrack_%'")
        conn.execute("DELETE FROM users WHERE id LIKE 'user_ttrack_%'")


def create_track_alert(
    user_id="user_ttrack_asha",
    status="active",
    token="test_token_active_123",
    lat=28.5433,
    lon=77.2065,
    street_name="Hauz Khas Village Road",
    hours_ago=0,
    points=None,
):
    """Helper to insert an alert and locations directly for deterministic tracking tests."""
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, name, email, phone) VALUES (?, ?, ?, ?)",
            (user_id, "Track Asha", f"{user_id}@test.com", "9876543210"),
        )
        created_str = (datetime.utcnow() - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            """
            INSERT INTO sos_alerts (
                user_id, status, start_lat, start_lon, last_lat, last_lon,
                street_name, track_token, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, status, lat, lon, lat, lon, street_name, token, created_str),
        )
        alert_id = cur.lastrowid

        pts = points or [(lat, lon), (lat + 0.0005, lon + 0.0005)]
        for pt_lat, pt_lon in pts:
            conn.execute(
                "INSERT INTO sos_locations (alert_id, lat, lon, created_at) VALUES (?, ?, ?, ?)",
                (alert_id, pt_lat, pt_lon, created_str),
            )
    return alert_id, token


def test_01_active_token_returns_location_and_trail(client):
    """Active token returns status, coordinates, trail polyline, and street name."""
    alert_id, token = create_track_alert(
        lat=28.5433,
        lon=77.2065,
        street_name="Hauz Khas Village Road",
        points=[(28.5433, 77.2065), (28.5438, 77.2070)],
    )

    # 1. API endpoint
    resp = client.get(f"/api/track/{token}")
    assert resp.status_code == 200
    data = resp.get_json()

    assert data["status"] == "active"
    assert data["last_lat"] == 28.5433
    assert data["last_lon"] == 77.2065
    assert len(data["trail"]) == 2
    assert data["trail"][0] == [28.5433, 77.2065]
    assert data["trail"][1] == [28.5438, 77.2070]
    assert data["street_name"] == "Hauz Khas Village Road"
    assert "updated_at" in data

    # 2. HTML page endpoint
    page_resp = client.get(f"/track/{token}")
    assert page_resp.status_code == 200
    html = page_resp.get_data(as_text=True)
    assert "Call Emergency 112" in html
    assert "Hauz Khas Village Road" in html
    assert "LIVE TRACKING" in html
    assert "leaflet.js" in html


def test_02_unknown_token_returns_404(client):
    """Unknown or non-existent token returns 404 with friendly 'Link not found' state."""
    unknown = "completely_unknown_token_9999"

    # API returns 404 JSON
    resp = client.get(f"/api/track/{unknown}")
    assert resp.status_code == 404
    data = resp.get_json()
    assert "Link not found" in data["error"]
    assert data["status"] == "not_found"

    # HTML page returns 404 with friendly message
    page_resp = client.get(f"/track/{unknown}")
    assert page_resp.status_code == 404
    html = page_resp.get_data(as_text=True)
    assert "Link not found" in html or "Link Not Found" in html


def test_03_resolved_alert_shows_ended_state(client):
    """
    Resolved alerts (where track_token was cleared and recorded in ended_tracks)
    show friendly 'This alert has ended. The person has marked themselves safe.'
    """
    token = "test_token_resolve_safe_456"
    alert_id, _ = create_track_alert(token=token)

    # Invalidate token and store in ended_tracks as resolve does
    record_ended_track(token, "safe", alert_id=alert_id)
    with db_conn() as conn:
        conn.execute("UPDATE sos_alerts SET track_token = NULL, status = 'resolved' WHERE id = ?", (alert_id,))

    # API test
    resp = client.get(f"/api/track/{token}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ended"
    assert "This alert has ended. The person has marked themselves safe." in data["message"]

    # HTML test
    page_resp = client.get(f"/track/{token}")
    assert page_resp.status_code == 200
    html = page_resp.get_data(as_text=True)
    assert "This alert has ended. The person has marked themselves safe." in html


def test_04_cancelled_alert_shows_cancelled_state(client):
    """
    Cancelled alerts show friendly 'This alert was cancelled' message.
    """
    token = "test_token_cancelled_789"
    alert_id, _ = create_track_alert(token=token)

    # Record cancellation into ended_tracks and clear active token
    record_ended_track(token, "cancelled", alert_id=alert_id)
    with db_conn() as conn:
        conn.execute("UPDATE sos_alerts SET track_token = NULL, status = 'cancelled' WHERE id = ?", (alert_id,))

    # API test
    resp = client.get(f"/api/track/{token}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "cancelled"
    assert "This alert was cancelled" in data["message"]

    # HTML test
    page_resp = client.get(f"/track/{token}")
    assert page_resp.status_code == 200
    html = page_resp.get_data(as_text=True)
    assert "This alert was cancelled" in html


def test_05_expired_alert_shows_expired_state(client):
    """
    Alerts older than TRACK_LINK_TTL_HOURS (6 hours) show 'This link has expired'.
    """
    token = "test_token_expired_999"
    # Created 7 hours ago (> 6 hours TTL)
    alert_id, _ = create_track_alert(token=token, hours_ago=7)

    # API test
    resp = client.get(f"/api/track/{token}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "expired"
    assert "This link has expired" in data["message"]

    # HTML test
    page_resp = client.get(f"/track/{token}")
    assert page_resp.status_code == 200
    html = page_resp.get_data(as_text=True)
    assert "This link has expired" in html


def test_06_response_has_no_pii(client):
    """
    Privacy verification:
    The API and page must NEVER include user's phone, email, user_id, contact details, or other PII.
    """
    token = "test_token_pii_check_321"
    alert_id, _ = create_track_alert(
        user_id="user_ttrack_secret",
        token=token,
        street_name="Near Deer Park Lake",
    )

    # Add contacts with real phone numbers for the user in DB
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO trusted_contacts (user_id, name, phone, relation) VALUES (?, ?, ?, ?)",
            ("user_ttrack_secret", "Mom", "9876543299", "Mother"),
        )

    resp = client.get(f"/api/track/{token}")
    assert resp.status_code == 200
    data = resp.get_json()

    # 1. Key check
    forbidden_keys = {"user_id", "email", "phone", "contacts", "recipient", "password"}
    for k in data.keys():
        assert k.lower() not in forbidden_keys, f"Found forbidden key {k} in track API response!"

    # 2. String/regex scan across entire JSON response
    raw_json = json.dumps(data)
    # Check for user_id
    assert "user_ttrack_secret" not in raw_json
    # Check for email
    email_pattern = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
    assert not email_pattern.search(raw_json), f"Found email in response: {email_pattern.findall(raw_json)}"
    # Check for 10-digit Indian phone numbers
    phone_pattern = re.compile(r"\b[6-9]\d{9}\b")
    assert not phone_pattern.search(raw_json), f"Found phone in response: {phone_pattern.findall(raw_json)}"

    # 3. HTML page check
    page_resp = client.get(f"/track/{token}")
    html = page_resp.get_data(as_text=True)
    assert "user_ttrack_secret" not in html
    assert f"{alert_id}@test.com" not in html
    assert "user_ttrack_secret@test.com" not in html
    assert "9876543299" not in html
    assert "9876543210" not in html


def test_07_cache_control_header_is_set(client):
    """Ensure Cache-Control: no-store is returned on both API and HTML responses."""
    token = "test_token_headers_888"
    create_track_alert(token=token)

    api_resp = client.get(f"/api/track/{token}")
    assert "no-store" in api_resp.headers.get("Cache-Control", "")

    page_resp = client.get(f"/track/{token}")
    assert "no-store" in page_resp.headers.get("Cache-Control", "")


def test_08_rate_limit_works(client):
    """
    Rate limiting:
    Maximum 60 requests per minute per IP. The 61st request returns 429 Too Many Requests.
    """
    token = "test_token_ratelimit_777"
    create_track_alert(token=token)

    # First 60 requests succeed
    for i in range(60):
        r = client.get(f"/api/track/{token}", headers={"X-Forwarded-For": "192.168.1.100"})
        assert r.status_code == 200, f"Request {i+1} failed unexpectedly with {r.status_code}"

    # 61st request is blocked by rate limit
    r61 = client.get(f"/api/track/{token}", headers={"X-Forwarded-For": "192.168.1.100"})
    assert r61.status_code == 429
    data = r61.get_json()
    assert "Rate limit exceeded" in data["error"]
    assert data["status"] == "rate_limited"

    # Different IP is not blocked
    r_diff = client.get(f"/api/track/{token}", headers={"X-Forwarded-For": "192.168.1.101"})
    assert r_diff.status_code == 200


def test_09_sos_enabled_false_returns_404(client, monkeypatch):
    """When SOS_ENABLED is False, /track/<token> and /api/track/<token> return 404."""
    token = "test_token_disabled_404"
    create_track_alert(token=token)

    monkeypatch.setattr(config, "SOS_ENABLED", False)

    api_resp = client.get(f"/api/track/{token}")
    assert api_resp.status_code == 404

    page_resp = client.get(f"/track/{token}")
    assert page_resp.status_code == 404
