"""
tests/test_notifier.py — Test Notification System & Providers (Milestone 2).

Required tests:
1. TwilioProvider selected with missing credentials: the app still starts,
   and the send is recorded as "failed" with an error message.
2. One recipient fails while the other two still succeed.
3. Phone numbers are masked in console logs (never the full number).
4. Numbers not on the allowed list are recorded as "simulated".
5. The WhatsApp link is correctly URL-encoded.
6. Test notification endpoint (POST /api/sos/test-notification).
"""

import os
import logging
import urllib.parse
from unittest.mock import MagicMock, patch
import pytest

from app import app
from db import db_conn
from notifier import (
    MockProvider,
    TwilioProvider,
    send_sms,
    wa_link,
    get_provider,
)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def cleanup_notifications():
    """Ensure tests are isolated and clean up notifications and test alerts."""
    yield
    with db_conn() as conn:
        conn.execute("DELETE FROM sos_notifications")
        conn.execute("DELETE FROM sos_alerts WHERE status = 'test'")


def test_twilio_missing_credentials_fails_gracefully(monkeypatch):
    """
    Test 1: TwilioProvider selected with missing credentials.
    The app still runs, and the send is recorded as 'failed' with an error message.
    """
    monkeypatch.setenv("SOS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "")

    prov = TwilioProvider(account_sid="", auth_token="", from_number="")
    res = prov.send_sms("+919876543210", "Emergency test alert")

    assert res["status"] == "failed"
    assert res["error"] is not None
    assert "Missing Twilio credentials" in res["error"]

    # Verify recorded in DB
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sos_notifications WHERE id = ?", (res["notification_id"],)
        ).fetchone()
        assert row is not None
        assert row["status"] == "failed"
        assert "Missing Twilio credentials" in row["error"]


def test_one_recipient_fails_others_succeed(monkeypatch):
    """
    Test 2: One recipient fails while the other two still succeed.
    A failure must never crash the flow or block other recipients.
    """
    monkeypatch.setenv("SOS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest1234567890abcdef1234567890")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "mock_auth_token_secret_12345")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15005550006")
    monkeypatch.setenv("TWILIO_ALLOWED_NUMBERS", "")

    recipients = ["+919876543211", "+919876543212", "+919876543213"]
    fail_phone = "+919876543212"

    class FakeMessage:
        sid = "SM_mock_sid_12345"

    class FakeMessages:
        def create(self, to, from_, body):
            if to == fail_phone:
                raise RuntimeError("Twilio Gateway Timeout / Carrier Error")
            return FakeMessage()

    class FakeClient:
        def __init__(self, sid, token):
            self.messages = FakeMessages()

    prov = TwilioProvider()
    results = []

    # Patch the lazy import of twilio.rest.Client
    with patch.dict("sys.modules", {"twilio": MagicMock(), "twilio.rest": MagicMock(Client=FakeClient)}):
        for phone in recipients:
            res = prov.send_sms(to=phone, body="Multi-recipient emergency broadcast")
            results.append(res)

    assert len(results) == 3
    assert results[0]["status"] == "sent"
    assert results[0]["error"] is None

    assert results[1]["status"] == "failed"
    assert "Twilio Gateway Timeout" in results[1]["error"]

    assert results[2]["status"] == "sent"
    assert results[2]["error"] is None

    # Verify all 3 recorded in DB
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT recipient_ref, status, error FROM sos_notifications ORDER BY id"
        ).fetchall()
        assert len(rows) == 3
        status_by_phone = {r["recipient_ref"]: r["status"] for r in rows}
        assert status_by_phone["+919876543211"] == "sent"
        assert status_by_phone["+919876543212"] == "failed"
        assert status_by_phone["+919876543213"] == "sent"


def test_phone_numbers_masked_in_logs(caplog):
    """
    Test 3: Phone numbers are masked in console logs (never the full number).
    """
    caplog.set_level(logging.INFO)
    test_phone = "+919876543210"
    raw_digits = "9876543210"

    prov = MockProvider()
    prov.send_sms(test_phone, "Testing privacy logging")

    # The full number or raw 10 digits must never appear in logs
    assert test_phone not in caplog.text
    assert raw_digits not in caplog.text

    # The masked number (preserving only last 4 digits) must appear
    assert "3210" in caplog.text
    assert "*" in caplog.text


def test_numbers_not_on_allowed_list_recorded_as_simulated(monkeypatch):
    """
    Test 4: Numbers not on the allowed list are recorded as "simulated".
    """
    monkeypatch.setenv("SOS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest1234567890abcdef1234567890")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "mock_auth_token_secret_12345")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15005550006")
    monkeypatch.setenv("TWILIO_ALLOWED_NUMBERS", "+919876543211, +919876543212")

    mock_client = MagicMock()
    with patch.dict("sys.modules", {"twilio": MagicMock(), "twilio.rest": MagicMock(Client=mock_client)}):
        prov = TwilioProvider()

        # Sending to a number NOT on allowed list -> recorded as simulated, client NOT called
        res_unallowed = prov.send_sms("+919876543299", "SOS to non-whitelisted number")
        assert res_unallowed["status"] == "simulated"
        mock_client.return_value.messages.create.assert_not_called()

        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sos_notifications WHERE id = ?", (res_unallowed["notification_id"],)
            ).fetchone()
            assert row["status"] == "simulated"

        # Sending to a number ON allowed list -> client IS called
        res_allowed = prov.send_sms("+919876543211", "SOS to whitelisted number")
        assert mock_client.return_value.messages.create.called


def test_whatsapp_link_url_encoded():
    """
    Test 5: The WhatsApp link is correctly URL-encoded.
    """
    phone = "+91 98765 43210"
    body = "SOS Alert! Asha needs help: https://rakshanet.in/track?alert=123&token=abc"

    link = wa_link(phone, body)

    # 1. Starts with https://wa.me/<digits>
    assert link.startswith("https://wa.me/919876543210?text=")

    # 2. No raw whitespace
    assert " " not in link

    # 3. Encoded query string is fully reversible
    encoded_text = link.split("?text=")[1]
    decoded_text = urllib.parse.unquote(encoded_text)
    assert decoded_text == body


def test_test_notification_endpoint_auth_and_demo(client, monkeypatch):
    """
    Test 6: POST /api/sos/test-notification requires DEMO_MODE and login.
    """
    # 1. Unauthenticated -> 401
    res = client.post("/api/sos/test-notification", json={})
    assert res.status_code == 401

    # 2. Authenticated but DEMO_MODE=False -> 403
    with client.session_transaction() as sess:
        sess["user_id"] = "user_asha"

    with patch("config.DEMO_MODE", False):
        res = client.post("/api/sos/test-notification", json={})
        assert res.status_code == 403

    # 3. Authenticated and DEMO_MODE=True -> 200 with results
    res = client.post("/api/sos/test-notification", json={})
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["count"] >= 1
    for r in data["results"]:
        assert r["status"] == "simulated"
        assert "wa_link" in r
        assert "https://wa.me/" in r["wa_link"]
        assert "*" in r["phone"]

    # 4. Custom phone specified
    res_custom = client.post(
        "/api/sos/test-notification",
        json={"phone": "+919899988877", "body": "Custom test alert"},
    )
    assert res_custom.status_code == 200
    custom_data = res_custom.get_json()
    assert custom_data["count"] == 1
    assert "3211" not in custom_data["results"][0]["phone"]
    assert "8877" in custom_data["results"][0]["phone"]
