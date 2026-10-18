"""
tests/test_phase2.py — Pytest suite for Phase 2 Community Verification,
Incident Reporting, Anti-abuse, and Scoring Integration.
"""

import pytest
import os
import json
from app import app, startup
from config import VERIFY_THRESHOLD, VOTE_RADIUS_M
from db import db_conn
import app as app_module

@pytest.fixture(scope="session", autouse=True)
def setup_server():
    """Ensure graph and startup state are loaded once for the session."""
    if app_module.G is None:
        startup()

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

@pytest.fixture(autouse=True)
def clean_test_incidents():
    """Wipe any reports created by user accounts so tests are idempotent and independent."""
    with db_conn() as conn:
        conn.execute("DELETE FROM incident_votes WHERE user_id LIKE 'user_%'")
        conn.execute("DELETE FROM incidents WHERE reporter_id LIKE 'user_%'")
    yield
    with db_conn() as conn:
        conn.execute("DELETE FROM incident_votes WHERE user_id LIKE 'user_%'")
        conn.execute("DELETE FROM incidents WHERE reporter_id LIKE 'user_%'")

def login_as(client, user_id, name="Test User"):
    """Helper to ensure user exists in DB and set session user_id."""
    with db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, name, email) VALUES (?, ?, ?)",
            (user_id, name, f"{user_id}@test.com"),
        )
    with client.session_transaction() as sess:
        sess["user_id"] = user_id

def test_self_vote_rejected(client):
    """User cannot vote on their own incident report."""
    uid = "user_t_self"
    login_as(client, uid)
    res = client.post("/api/incidents", json={
        "lat": 28.5510,
        "lon": 77.2040,
        "type": "poor_lighting",
        "note": "Dark stretch",
        "incident_time": "2026-10-13 21:00:00",
    })
    assert res.status_code == 201
    inc_id = res.get_json()["incident"]["id"]

    vote_res = client.post(f"/api/incidents/{inc_id}/vote", json={
        "lat": 28.5510,
        "lon": 77.2040,
        "simulated": True,
    })
    assert vote_res.status_code == 400
    assert "own" in vote_res.get_json()["error"].lower()

def test_duplicate_vote_rejected(client):
    """Same user cannot vote more than once on the same incident."""
    login_as(client, "user_t_dup1")
    res = client.post("/api/incidents", json={
        "lat": 28.5440,
        "lon": 77.2060,
        "type": "unsafe_area",
        "note": "Deserted lane",
    })
    assert res.status_code == 201
    inc_id = res.get_json()["incident"]["id"]

    login_as(client, "user_t_dup2")
    vote1 = client.post(f"/api/incidents/{inc_id}/vote", json={
        "lat": 28.5440,
        "lon": 77.2060,
        "simulated": True,
    })
    assert vote1.status_code == 200

    vote2 = client.post(f"/api/incidents/{inc_id}/vote", json={
        "lat": 28.5440,
        "lon": 77.2060,
        "simulated": True,
    })
    assert vote2.status_code == 400
    assert "already" in vote2.get_json()["error"].lower()

def test_far_away_vote_rejected(client):
    """Vote without simulated flag outside VOTE_RADIUS_M is rejected."""
    login_as(client, "user_t_dist1")
    res = client.post("/api/incidents", json={
        "lat": 28.5520,
        "lon": 77.2050,
        "type": "harassment",
        "note": "Catcalling reported",
    })
    assert res.status_code == 201
    inc_id = res.get_json()["incident"]["id"]

    login_as(client, "user_t_dist2")
    vote_res = client.post(f"/api/incidents/{inc_id}/vote", json={
        "lat": 28.6304,
        "lon": 77.2177,
        "simulated": False,
    })
    assert vote_res.status_code == 400
    assert "too far" in vote_res.get_json()["error"].lower()

def test_threshold_flips_status_exactly_at_configured_count(client):
    """Incident status flips from pending to verified when confirmations == VERIFY_THRESHOLD."""
    login_as(client, "user_t_thresh1")
    res = client.post("/api/incidents", json={
        "lat": 28.5480,
        "lon": 77.2080,
        "type": "poor_lighting",
        "note": "Light broken",
    })
    assert res.status_code == 201
    inc_id = res.get_json()["incident"]["id"]
    assert res.get_json()["incident"]["status"] == "pending"
    assert res.get_json()["incident"]["upvotes"] == 1

    # Vote 1 (user 2): Total 2 confirmations -> still pending
    login_as(client, "user_t_thresh2")
    v1 = client.post(f"/api/incidents/{inc_id}/vote", json={"simulated": True})
    assert v1.status_code == 200
    assert v1.get_json()["confirmations"] == 2
    assert v1.get_json()["verified"] is False

    # Vote 2 (user 3): Total 3 confirmations (equals VERIFY_THRESHOLD=3) -> flips to verified
    login_as(client, "user_t_thresh3")
    v2 = client.post(f"/api/incidents/{inc_id}/vote", json={"simulated": True})
    assert v2.status_code == 200
    assert v2.get_json()["confirmations"] == 3
    assert v2.get_json()["verified"] is True
    assert v2.get_json()["newly_verified"] is True

    # Check database status
    with db_conn() as conn:
        row = conn.execute("SELECT status, upvotes FROM incidents WHERE id = ?", (inc_id,)).fetchone()
        assert row["status"] == "verified"
        assert row["upvotes"] == 3

def test_pending_incidents_do_not_affect_scores(client):
    """A pending incident must NOT decrease safety scores for an edge."""
    u, v, key = 6469652559, 1024317388, 0
    score_before = app_module.HOUR_SCORES[23].get((u, v, key))

    login_as(client, "user_t_pend")
    res = client.post("/api/incidents", json={
        "lat": 28.5433,
        "lon": 77.2065,
        "type": "poor_lighting",
        "note": "Test pending score impact",
    })
    assert res.status_code == 201

    score_after = app_module.HOUR_SCORES[23].get((u, v, key))
    assert score_before == score_after, "Pending incident should not modify score cache!"

from scoring import update_incremental_scores

def test_verification_changes_edge_score(client):
    """When an incident reaches verified threshold, the affected edge score must drop."""
    import osmnx as ox
    lat, lon = 28.5433, 77.2065
    u, v, k = ox.nearest_edges(app_module.G, lon, lat)
    edge_u, edge_v, edge_key = int(u), int(v), int(k)
    with db_conn() as conn:
        update_incremental_scores(app_module.G, app_module.HOUR_SCORES, edge_u, edge_v, edge_key, conn)
    score_before = app_module.HOUR_SCORES[23].get((edge_u, edge_v, edge_key))

    login_as(client, "user_t_score1")
    res = client.post("/api/incidents", json={
        "lat": lat,
        "lon": lon,
        "type": "poor_lighting",
        "incident_time": "2026-10-13 23:00:00",
        "note": "Test verification score impact",
    })
    assert res.status_code == 201
    inc_data = res.get_json()["incident"]
    inc_id = inc_data["id"]

    # Two distinct users vote to reach VERIFY_THRESHOLD=3
    login_as(client, "user_t_score2")
    client.post(f"/api/incidents/{inc_id}/vote", json={"simulated": True})

    login_as(client, "user_t_score3")
    v_res = client.post(f"/api/incidents/{inc_id}/vote", json={"simulated": True})
    assert v_res.get_json()["verified"] is True

    score_after = app_module.HOUR_SCORES[23].get((edge_u, edge_v, edge_key))
    assert score_after < score_before, f"Score should drop after verification: {score_before} -> {score_after}"

def test_rate_limit_enforced(client):
    """User cannot file more than 5 reports in a single hour."""
    uid = "user_t_rate"
    login_as(client, uid)

    for i in range(5):
        res = client.post("/api/incidents", json={
            "lat": 28.5380 + i * 0.003,
            "lon": 77.1950 + i * 0.003,
            "type": "other",
            "note": f"Report #{i+1}",
        })
        assert res.status_code == 201, f"Report {i+1} failed with {res.get_json()}"

    res6 = client.post("/api/incidents", json={
        "lat": 28.5600,
        "lon": 77.2150,
        "type": "other",
        "note": "Report #6",
    })
    assert res6.status_code == 429
    assert "rate limit" in res6.get_json()["error"].lower()

def test_duplicate_report_detection(client):
    """Reporting the same hazard type within 50 m in last 2 hours returns 409 with duplicate id."""
    login_as(client, "user_t_dup_rep1")
    res1 = client.post("/api/incidents", json={
        "lat": 28.5530,
        "lon": 77.2070,
        "type": "harassment",
        "note": "Catcalling near bus stop",
    })
    assert res1.status_code == 201
    first_id = res1.get_json()["incident"]["id"]

    login_as(client, "user_t_dup_rep2")
    res2 = client.post("/api/incidents", json={
        "lat": 28.55315,
        "lon": 77.2070,
        "type": "harassment",
        "note": "Second report near bus stop",
    })
    assert res2.status_code == 409
    assert res2.get_json()["duplicate_incident_id"] == first_id
