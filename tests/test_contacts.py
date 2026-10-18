"""
tests/test_contacts.py — Test Trusted Contacts endpoints (Milestone 1).
"""

import pytest
from app import app
from db import db_conn

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_list_contacts_demo_user(client):
    """Demo user Asha starts with 2 seeded contacts."""
    with client.session_transaction() as sess:
        sess["user_id"] = "user_asha"
    res = client.get("/api/contacts")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["count"] == 2
    assert len(data["contacts"]) == 2
    for c in data["contacts"]:
        assert "phone_masked" in c
        assert "*" in c["phone_masked"]

def test_add_contact_and_limit_enforced(client):
    """User can add up to 3 contacts, 4th must fail with 400."""
    uid = "user_t_contact"
    with db_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (id, name, email) VALUES (?, ?, ?)", (uid, "Test Contacts", "tc@test.com"))
        conn.execute("DELETE FROM trusted_contacts WHERE user_id = ?", (uid,))

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    # Contact 1: 10 digits -> normalized to +91
    r1 = client.post("/api/contacts", json={"name": "Alice", "phone": "9811122233", "relation": "Friend"})
    assert r1.status_code == 201
    assert r1.get_json()["contact"]["phone"] == "+919811122233"

    # Contact 2: E.164 format
    r2 = client.post("/api/contacts", json={"name": "Bob", "phone": "+919822233344", "relation": "Family"})
    assert r2.status_code == 201

    # Contact 3:
    r3 = client.post("/api/contacts", json={"name": "Charlie", "phone": "9833344455", "relation": "Colleague"})
    assert r3.status_code == 201

    # Contact 4: Limit exceeded -> 400
    r4 = client.post("/api/contacts", json={"name": "Dave", "phone": "9844455566", "relation": "Other"})
    assert r4.status_code == 400
    assert "limit" in r4.get_json()["error"].lower()

    # Cleanup
    with db_conn() as conn:
        conn.execute("DELETE FROM trusted_contacts WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))

def test_duplicate_phone_rejected(client):
    """Same phone number cannot be added twice by same user."""
    uid = "user_t_dup_c"
    with db_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (id, name, email) VALUES (?, ?, ?)", (uid, "Dup Test", "dup@test.com"))
        conn.execute("DELETE FROM trusted_contacts WHERE user_id = ?", (uid,))

    with client.session_transaction() as sess:
        sess["user_id"] = uid

    r1 = client.post("/api/contacts", json={"name": "Sister 1", "phone": "9899988877"})
    assert r1.status_code == 201

    r2 = client.post("/api/contacts", json={"name": "Sister 2", "phone": "+919899988877"})
    assert r2.status_code == 409

    with db_conn() as conn:
        conn.execute("DELETE FROM trusted_contacts WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))

def test_delete_contact(client):
    """User can delete their own contact, cannot delete another user's contact."""
    with client.session_transaction() as sess:
        sess["user_id"] = "user_t_del1"
    with db_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (id, name, email) VALUES ('user_t_del1', 'Del 1', 'd1@test.com')")
        conn.execute("INSERT OR IGNORE INTO users (id, name, email) VALUES ('user_t_del2', 'Del 2', 'd2@test.com')")
        conn.execute("DELETE FROM trusted_contacts WHERE user_id IN ('user_t_del1', 'user_t_del2')")

    add_res = client.post("/api/contacts", json={"name": "Contact 1", "phone": "9800011122"})
    assert add_res.status_code == 201
    cid = add_res.get_json()["contact"]["id"]

    # User 2 tries to delete user 1's contact -> 404
    with client.session_transaction() as sess:
        sess["user_id"] = "user_t_del2"
    del_fail = client.delete(f"/api/contacts/{cid}")
    assert del_fail.status_code == 404

    # User 1 deletes own contact -> 200
    with client.session_transaction() as sess:
        sess["user_id"] = "user_t_del1"
    del_ok = client.delete(f"/api/contacts/{cid}")
    assert del_ok.status_code == 200

    with db_conn() as conn:
        conn.execute("DELETE FROM trusted_contacts WHERE user_id IN ('user_t_del1', 'user_t_del2')")
        conn.execute("DELETE FROM users WHERE id IN ('user_t_del1', 'user_t_del2')")
