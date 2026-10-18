"""
tests/test_regression_phase12.py — Locks in Phase 1 & 2 golden behavior.
Ensures Phase 3 additions never alter route distances, safety scores, search,
or voting mechanics.
"""

import pytest
import json
import os
from app import app, startup
import app as app_module
from config import DEMO_ROUTES, VERIFY_THRESHOLD
from db import db_conn

@pytest.fixture(scope="session", autouse=True)
def setup_server():
    if app_module.G is None:
        startup()

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_golden_routes_and_scores(client):
    """Verify route distances and scores match saved golden values."""
    golden_path = os.path.join(os.path.dirname(__file__), "..", "data", "golden_phase12.json")
    assert os.path.exists(golden_path), "golden_phase12.json missing!"
    with open(golden_path, "r") as f:
        golden = json.load(f)

    for p in DEMO_ROUTES:
        name = p["name"]
        assert name in golden, f"Preset {name} not in golden values"
        for h in [18, 23]:
            res = client.get("/api/route", query_string={
                "start_lat": p["start"][0],
                "start_lon": p["start"][1],
                "end_lat": p["end"][0],
                "end_lon": p["end"][1],
                "hour": h,
            })
            assert res.status_code == 200
            data = res.get_json()
            g_vals = golden[name][str(h)]
            assert pytest.approx(data["shortest"]["total_distance_m"], rel=1e-2) == g_vals["short_dist"]
            assert pytest.approx(data["shortest"]["avg_score"], rel=1e-2) == g_vals["short_score"]
            assert pytest.approx(data["safest"]["total_distance_m"], rel=1e-2) == g_vals["safe_dist"]
            assert pytest.approx(data["safest"]["avg_score"], rel=1e-2) == g_vals["safe_score"]

def test_search_hauz_non_empty(client):
    """Verify search still returns results for 'hauz'."""
    res = client.get("/api/search?q=hauz")
    assert res.status_code == 200
    data = res.get_json()
    results = data.get("results") if isinstance(data, dict) else data
    assert isinstance(results, list)
    assert len(results) >= 1
    assert any("hauz" in r["label"].lower() for r in results)

def test_vote_threshold_flips_at_verify_threshold(client):
    """Verify threshold flip behavior still works at exactly VERIFY_THRESHOLD."""
    with db_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (id, name, email) VALUES ('u_reg_1', 'Reg 1', 'r1@test.com')")
        conn.execute("INSERT OR IGNORE INTO users (id, name, email) VALUES ('u_reg_2', 'Reg 2', 'r2@test.com')")
        conn.execute("INSERT OR IGNORE INTO users (id, name, email) VALUES ('u_reg_3', 'Reg 3', 'r3@test.com')")

    with client.session_transaction() as sess:
        sess["user_id"] = "u_reg_1"

    res = client.post("/api/incidents", json={
        "lat": 28.5522,
        "lon": 77.2045,
        "type": "other",
        "note": "Regression test incident",
    })
    assert res.status_code == 201
    inc_id = res.get_json()["incident"]["id"]

    try:
        # User 2 votes -> 2 confirmations, still pending
        with client.session_transaction() as sess:
            sess["user_id"] = "u_reg_2"
        v1 = client.post(f"/api/incidents/{inc_id}/vote", json={"simulated": True})
        assert v1.status_code == 200
        assert v1.get_json()["verified"] is False
        assert v1.get_json()["confirmations"] == 2

        # User 3 votes -> 3 confirmations -> flips to verified
        with client.session_transaction() as sess:
            sess["user_id"] = "u_reg_3"
        v2 = client.post(f"/api/incidents/{inc_id}/vote", json={"simulated": True})
        assert v2.status_code == 200
        assert v2.get_json()["verified"] is True
        assert v2.get_json()["confirmations"] == VERIFY_THRESHOLD
    finally:
        with db_conn() as conn:
            conn.execute("DELETE FROM incident_votes WHERE incident_id = ?", (inc_id,))
            conn.execute("DELETE FROM incidents WHERE id = ?", (inc_id,))
            conn.execute("DELETE FROM users WHERE id IN ('u_reg_1', 'u_reg_2', 'u_reg_3')")
