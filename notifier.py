"""
notifier.py — Phase 3 Notification Service for RakshaNet Smart SOS.

Provides multi-channel emergency notification dispatch:
- MockProvider: Default zero-cost simulator. Logs masked numbers and records status='simulated'.
- TwilioProvider: Live SMS via Twilio REST API with lazy import and allowed-number filtering.
- wa_link: Generates pre-filled WhatsApp click-to-chat links with URL-encoded emergency text.
- POST /api/sos/test-notification: Endpoint for testing notifications in DEMO_MODE.
"""

import os
import re
import logging
import urllib.parse
from abc import ABC, abstractmethod
from flask import Blueprint, request, jsonify, session

from db import db_conn
from contacts import mask_phone, normalize_phone
import config
from config import (
    SOS_PROVIDER,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_FROM_NUMBER,
)

logger = logging.getLogger(__name__)

notifier_bp = Blueprint("notifier", __name__)


# ── WhatsApp Link Helper ─────────────────────────────────────────────────────

def wa_link(to: str, body: str) -> str:
    """
    Generate a WhatsApp click-to-chat link (https://wa.me/<number>?text=<urlencoded>).
    Phone number must be digits only without leading +, dashes, or spaces.
    The message text is percent-encoded.
    """
    clean_num = re.sub(r"[^\d]", "", str(to or "").strip())
    encoded_body = urllib.parse.quote(str(body or "").strip(), safe="")
    return f"https://wa.me/{clean_num}?text={encoded_body}"


# ── Database Helpers ─────────────────────────────────────────────────────────

def record_notification(
    alert_id: int,
    channel: str,
    recipient_type: str,
    recipient_ref: str,
    status: str,
    error: str = None
) -> int:
    """
    Record a notification attempt in the sos_notifications table.
    Ensures a valid alert_id exists to satisfy foreign key constraints.
    Returns the inserted row ID.
    """
    with db_conn() as conn:
        # Check if alert_id exists; if not or if 0, attach to a test alert row
        valid_alert = False
        if alert_id:
            row = conn.execute("SELECT id FROM sos_alerts WHERE id = ?", (alert_id,)).fetchone()
            if row:
                valid_alert = True

        if not valid_alert:
            test_alert = conn.execute(
                "SELECT id FROM sos_alerts WHERE status = 'test' LIMIT 1"
            ).fetchone()
            if test_alert:
                alert_id = test_alert["id"]
            else:
                cur = conn.execute(
                    """
                    INSERT INTO sos_alerts (user_id, status, start_lat, start_lon, track_token)
                    VALUES ('user_asha', 'test', 28.5560, 77.2060, 'test_alert_token')
                    """
                )
                alert_id = cur.lastrowid

        cursor = conn.execute(
            """
            INSERT INTO sos_notifications (
                alert_id, channel, recipient_type, recipient_ref, status, error
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                alert_id,
                channel or "sms",
                recipient_type or "contact",
                recipient_ref,
                status,
                str(error) if error else None,
            ),
        )
        return cursor.lastrowid


# ── Allowed Numbers Helper (Twilio sandbox / dev guard) ──────────────────────

def _get_allowed_numbers():
    """
    Parse TWILIO_ALLOWED_NUMBERS from environment variable.
    Returns a set of normalized and raw strings for comparison.
    """
    raw = os.environ.get("TWILIO_ALLOWED_NUMBERS", "")
    if not raw:
        return set()
    nums = set()
    for item in re.split(r"[,\s;]+", raw.strip()):
        if item:
            nums.add(item)
            norm = normalize_phone(item)
            if norm:
                nums.add(norm)
            digits = re.sub(r"[^\d]", "", item)
            if digits:
                nums.add(digits)
    return nums


def _is_number_allowed(to: str, allowed_set: set) -> bool:
    """Check if destination number matches any entry in the allowed set."""
    if not allowed_set:
        return False
    norm = normalize_phone(to) or to
    digits = re.sub(r"[^\d]", "", to)
    return (to in allowed_set or norm in allowed_set or digits in allowed_set)


# ── Notification Providers ───────────────────────────────────────────────────

class BaseProvider(ABC):
    @abstractmethod
    def send_sms(self, to: str, body: str, alert_id: int = 0, recipient_type: str = "contact") -> dict:
        """Send an SMS or simulate sending. Must never crash on delivery failure."""
        pass


class MockProvider(BaseProvider):
    """
    Default mock notification provider.
    Sends nothing, logs with phone masked, writes 'simulated' to DB.
    """
    def send_sms(self, to: str, body: str, alert_id: int = 0, recipient_type: str = "contact") -> dict:
        masked = mask_phone(to)
        logger.info("[MockProvider] Simulated SMS to %s: %s", masked, body)
        notif_id = record_notification(
            alert_id=alert_id,
            channel="sms",
            recipient_type=recipient_type,
            recipient_ref=to,
            status="simulated",
            error=None,
        )
        return {
            "status": "simulated",
            "to": to,
            "to_masked": masked,
            "notification_id": notif_id,
            "error": None,
        }


class TwilioProvider(BaseProvider):
    """
    Twilio SMS provider.
    Uses lazy import so application boots even if twilio library is not installed.
    Catches all exceptions so a failure never crashes the flow or blocks other recipients.
    """
    def __init__(self, account_sid: str = None, auth_token: str = None, from_number: str = None):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number

    def send_sms(self, to: str, body: str, alert_id: int = 0, recipient_type: str = "contact") -> dict:
        masked = mask_phone(to)
        allowed = _get_allowed_numbers()

        # Check allowed numbers list if configured
        if allowed and not _is_number_allowed(to, allowed):
            logger.info("[TwilioProvider] Phone %s not in TWILIO_ALLOWED_NUMBERS — simulated.", masked)
            notif_id = record_notification(
                alert_id=alert_id,
                channel="sms",
                recipient_type=recipient_type,
                recipient_ref=to,
                status="simulated",
                error=None,
            )
            return {
                "status": "simulated",
                "to": to,
                "to_masked": masked,
                "notification_id": notif_id,
                "error": None,
            }

        # Check credentials
        sid = self.account_sid or os.environ.get("TWILIO_ACCOUNT_SID") or TWILIO_ACCOUNT_SID
        token = self.auth_token or os.environ.get("TWILIO_AUTH_TOKEN") or TWILIO_AUTH_TOKEN
        from_num = self.from_number or os.environ.get("TWILIO_FROM_NUMBER") or TWILIO_FROM_NUMBER

        if not sid or not token or not from_num:
            err_msg = "Missing Twilio credentials (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, or TWILIO_FROM_NUMBER)"
            logger.error("[TwilioProvider] Failed sending SMS to %s: %s", masked, err_msg)
            notif_id = record_notification(
                alert_id=alert_id,
                channel="sms",
                recipient_type=recipient_type,
                recipient_ref=to,
                status="failed",
                error=err_msg,
            )
            return {
                "status": "failed",
                "to": to,
                "to_masked": masked,
                "notification_id": notif_id,
                "error": err_msg,
            }

        # Lazy import of twilio package
        try:
            from twilio.rest import Client
        except ImportError:
            err_msg = "twilio package is not installed (pip install twilio)"
            logger.error("[TwilioProvider] Failed sending SMS to %s: %s", masked, err_msg)
            notif_id = record_notification(
                alert_id=alert_id,
                channel="sms",
                recipient_type=recipient_type,
                recipient_ref=to,
                status="failed",
                error=err_msg,
            )
            return {
                "status": "failed",
                "to": to,
                "to_masked": masked,
                "notification_id": notif_id,
                "error": err_msg,
            }

        # Send via Twilio client
        try:
            client = Client(sid, token)
            message = client.messages.create(
                to=to,
                from_=from_num,
                body=body,
            )
            msg_sid = getattr(message, "sid", "unknown")
            logger.info("[TwilioProvider] Sent SMS to %s (sid=%s)", masked, msg_sid)
            notif_id = record_notification(
                alert_id=alert_id,
                channel="sms",
                recipient_type=recipient_type,
                recipient_ref=to,
                status="sent",
                error=None,
            )
            return {
                "status": "sent",
                "to": to,
                "to_masked": masked,
                "notification_id": notif_id,
                "message_sid": msg_sid,
                "error": None,
            }
        except Exception as e:
            err_msg = str(e)
            logger.error("[TwilioProvider] Twilio send failed for %s: %s", masked, err_msg)
            notif_id = record_notification(
                alert_id=alert_id,
                channel="sms",
                recipient_type=recipient_type,
                recipient_ref=to,
                status="failed",
                error=err_msg,
            )
            return {
                "status": "failed",
                "to": to,
                "to_masked": masked,
                "notification_id": notif_id,
                "error": err_msg,
            }


# ── Provider Factory & Global Dispatch ───────────────────────────────────────

def get_provider(provider_type: str = None) -> BaseProvider:
    """Return provider instance based on argument or SOS_PROVIDER setting."""
    p = (provider_type or os.environ.get("SOS_PROVIDER") or SOS_PROVIDER or "mock").lower().strip()
    if p == "twilio":
        return TwilioProvider()
    return MockProvider()


def send_sms(to: str, body: str, alert_id: int = 0, recipient_type: str = "contact", provider: BaseProvider = None) -> dict:
    """
    Common notification interface to send an SMS or simulated SMS.
    Returns result dict with status, to, to_masked, error, and notification_id.
    """
    prov = provider or get_provider()
    return prov.send_sms(to=to, body=body, alert_id=alert_id, recipient_type=recipient_type)


# ── Test Notification Endpoint ───────────────────────────────────────────────

@notifier_bp.route("/api/sos/test-notification", methods=["POST"])
def api_test_notification():
    """
    Send a test notification to user's trusted contacts or specified phone.
    Available only in DEMO_MODE, requires user authentication.
    """
    if not getattr(config, "DEMO_MODE", True):
        return jsonify({"error": "Test notification is only available in DEMO_MODE."}), 403

    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required. Please log in."}), 401

    with db_conn() as conn:
        user = conn.execute("SELECT id, name, phone FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"error": "User not found."}), 404
        user_name = user["name"] or "RakshaNet User"

    data = request.get_json(silent=True) or {}
    custom_phone = data.get("phone") or data.get("to")
    custom_body = data.get("body")

    recipients = []
    if custom_phone:
        recipients.append({"name": "Test Recipient", "phone": str(custom_phone).strip()})
    else:
        with db_conn() as conn:
            contacts = conn.execute(
                "SELECT name, phone FROM trusted_contacts WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
            for c in contacts:
                recipients.append({"name": c["name"], "phone": c["phone"]})

    if not recipients:
        return jsonify({
            "error": "No trusted contacts found. Add contacts first or provide 'phone' in the request body."
        }), 400

    results = []
    for r in recipients:
        phone = r["phone"]
        body = custom_body or f"RakshaNet SOS Test Alert: {user_name} has triggered a test emergency notification. No action required."
        sms_res = send_sms(to=phone, body=body, alert_id=0, recipient_type="contact")
        wa = wa_link(to=phone, body=body)
        results.append({
            "recipient_name": r["name"],
            "phone": mask_phone(phone),
            "status": sms_res["status"],
            "error": sms_res.get("error"),
            "wa_link": wa,
            "notification_id": sms_res.get("notification_id"),
        })

    return jsonify({
        "status": "ok",
        "provider": (os.environ.get("SOS_PROVIDER") or SOS_PROVIDER or "mock").lower(),
        "count": len(results),
        "results": results,
    }), 200
