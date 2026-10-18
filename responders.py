"""
responders.py — Phase 3 Milestone 4: Community Responders & Notifications for RakshaNet.

Features:
- POST /api/responders/optin: Volunteer opt-in/opt-out with safety disclaimer.
- notify_community_responders(alert_id, lat, lon): Geofenced dispatch (within RESPONDER_RADIUS_M)
  with strict privacy controls (victim identity & coordinates withheld until accepted).
- GET  /api/notifications: In-app inbox for responders.
- POST /api/notifications/<id>/respond: Accept ("on_my_way") or decline ("cant_help").
- GET  /api/sos/<id>/responders: Victim's dashboard view of nearby responders and statuses.
"""

import math
import json
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, session

import config
from config import RESPONDER_RADIUS_M, DEMO_MODE, SOS_ENABLED
from db import db_conn

logger = logging.getLogger(__name__)

responders_bp = Blueprint("responders", __name__)


# ── Guards & Error Handlers ───────────────────────────────────────────────────

@responders_bp.before_request
def check_sos_enabled():
    """Ensure responder endpoints return 404 when SOS_ENABLED is False."""
    if not getattr(config, "SOS_ENABLED", True):
        return jsonify({"error": "Smart SOS feature is disabled."}), 404


@responders_bp.errorhandler(Exception)
def handle_responders_exception(e):
    """Ensure uncaught responder errors return JSON and never crash the app."""
    logger.exception("[responders_bp] Unhandled exception: %s", str(e))
    return jsonify({"error": f"An error occurred in responders system: {str(e)}"}), 500


def _error(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _get_current_user_id():
    """Extract authenticated user ID from session or DEMO_MODE fallback."""
    uid = session.get("user_id")
    if uid:
        return uid
    if getattr(config, "DEMO_MODE", False):
        return "user_asha"
    return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two GPS coordinates."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


# ── Core Community Responder Dispatch ─────────────────────────────────────────

def notify_community_responders(alert_id: int, lat: float, lon: float) -> list:
    """
    Find opted-in, verified community responders within RESPONDER_RADIUS_M of (lat, lon),
    excluding the victim (alert owner).
    Writes an in-app notification to `notifications` and an audit row to `sos_notifications`.
    Returns list of notified responder records.
    Catches all exceptions so responder dispatch never crashes SOS start.
    """
    notified = []
    try:
        radius_m = getattr(config, "RESPONDER_RADIUS_M", 800)

        with db_conn() as conn:
            # 1. Fetch alert details (street_name, owner user_id)
            alert = conn.execute(
                "SELECT user_id, street_name FROM sos_alerts WHERE id = ?", (alert_id,)
            ).fetchone()
            if not alert:
                logger.warning("[responders] Alert %s not found for responder dispatch.", alert_id)
                return []

            owner_id = alert["user_id"]
            street_name = alert["street_name"] or "Nearby street"

            # 2. Query opted-in and verified responders with known locations
            candidates = conn.execute(
                """
                SELECT u.id, u.name, rl.lat, rl.lon
                FROM users u
                JOIN responder_locations rl ON u.id = rl.user_id
                WHERE u.is_responder = 1
                  AND u.responder_verified = 1
                  AND u.id != ?
                """,
                (owner_id,),
            ).fetchall()

            for cand in candidates:
                cand_id = cand["id"]
                cand_lat = cand["lat"]
                cand_lon = cand["lon"]

                dist = haversine_m(lat, lon, cand_lat, cand_lon)
                if dist <= radius_m:
                    approx_dist = int(round(dist / 50.0) * 50)
                    if approx_dist == 0:
                        approx_dist = 50

                    # Privacy: only street_name and approx distance, NO phone number, NO victim name, NO exact coordinates
                    payload = {
                        "alert_id": alert_id,
                        "street_name": street_name,
                        "approx_distance_m": approx_dist,
                        "status": "notified",
                    }
                    payload_json = json.dumps(payload)

                    # Insert in-app notification
                    conn.execute(
                        """
                        INSERT INTO notifications (user_id, type, payload_json, read, created_at)
                        VALUES (?, 'sos_responder_alert', ?, 0, datetime('now'))
                        """,
                        (cand_id, payload_json),
                    )

                    # Insert sos_notifications audit row
                    conn.execute(
                        """
                        INSERT INTO sos_notifications (
                            alert_id, channel, recipient_type, recipient_ref, status, error, created_at
                        ) VALUES (?, 'in_app', 'responder', ?, 'notified', NULL, datetime('now'))
                        """,
                        (alert_id, cand_id),
                    )

                    notified.append({
                        "responder_id": cand_id,
                        "approx_distance_m": approx_dist,
                        "status": "notified",
                    })

            logger.info("[responders] Notified %d community responders for alert %s", len(notified), alert_id)
    except Exception as e:
        logger.exception("[responders] Error notifying community responders: %s", str(e))

    return notified


# ── Responder Endpoints ───────────────────────────────────────────────────────

@responders_bp.route("/api/responders/optin", methods=["POST"])
def optin_responder():
    """
    Toggle community responder opt-in and record consent timestamp.
    Returns volunteer disclaimer.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return _error("Authentication required. Please log in.", 401)

    data = request.get_json(silent=True) or {}
    disclaimer = "Responders are volunteers, not police. In an emergency, call 112."

    with db_conn() as conn:
        user = conn.execute("SELECT id, is_responder FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return _error("User not found.", 404)

        current_optin = bool(user["is_responder"])
        if "opt_in" in data:
            new_optin = bool(data["opt_in"])
        else:
            new_optin = not current_optin

        if new_optin:
            conn.execute(
                """
                UPDATE users
                SET is_responder = 1, responder_consent_at = datetime('now')
                WHERE id = ?
                """,
                (user_id,),
            )
        else:
            conn.execute(
                """
                UPDATE users
                SET is_responder = 0
                WHERE id = ?
                """,
                (user_id,),
            )

        return jsonify({
            "status": "ok",
            "is_responder": new_optin,
            "disclaimer": disclaimer,
        }), 200


@responders_bp.route("/api/notifications", methods=["GET"])
def get_notifications():
    """Get current user's notifications, newest first."""
    user_id = _get_current_user_id()
    if not user_id:
        return _error("Authentication required. Please log in.", 401)

    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, type, payload_json, read, created_at
            FROM notifications
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()

        notifications = []
        for r in rows:
            item = dict(r)
            try:
                item["payload"] = json.loads(r["payload_json"]) if r["payload_json"] else {}
            except Exception:
                item["payload"] = {}
            notifications.append(item)

        return jsonify({
            "status": "ok",
            "count": len(notifications),
            "notifications": notifications,
        }), 200


@responders_bp.route("/api/notifications/<int:notif_id>/respond", methods=["POST"])
def respond_notification(notif_id: int):
    """
    Respond to an SOS notification (owner only).
    Body: {action: 'on_my_way' | 'cant_help'}.
    Stores action and timestamp.
    Privacy rule: Only after 'on_my_way' can the payload show exact coordinates.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return _error("Authentication required. Please log in.", 401)

    data = request.get_json(silent=True) or {}
    action = data.get("action", "").strip().lower()
    if action not in ("on_my_way", "cant_help"):
        return _error("action must be 'on_my_way' or 'cant_help'.", 400)

    with db_conn() as conn:
        notif = conn.execute("SELECT * FROM notifications WHERE id = ?", (notif_id,)).fetchone()
        if not notif:
            return _error("Notification not found.", 404)
        if notif["user_id"] != user_id:
            return _error("Access denied: only the notification owner can respond.", 403)

        try:
            payload = json.loads(notif["payload_json"]) if notif["payload_json"] else {}
        except Exception:
            payload = {}

        payload["status"] = action
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        payload["responded_at"] = now_str

        alert_id = payload.get("alert_id")

        # Privacy rule: only show exact coordinates after 'on_my_way'
        if action == "on_my_way" and alert_id:
            alert = conn.execute(
                "SELECT last_lat, last_lon, start_lat, start_lon FROM sos_alerts WHERE id = ?",
                (alert_id,),
            ).fetchone()
            if alert:
                lat = alert["last_lat"] or alert["start_lat"]
                lon = alert["last_lon"] or alert["start_lon"]
                payload["exact_lat"] = lat
                payload["exact_lon"] = lon
                payload["maps_link"] = f"https://maps.google.com/?q={lat:.5f},{lon:.5f}"

        # Update notification row
        updated_payload_json = json.dumps(payload)
        conn.execute(
            """
            UPDATE notifications
            SET payload_json = ?, read = 1
            WHERE id = ?
            """,
            (updated_payload_json, notif_id),
        )

        # Update status in sos_notifications audit trail
        if alert_id:
            conn.execute(
                """
                UPDATE sos_notifications
                SET status = ?
                WHERE alert_id = ? AND recipient_ref = ? AND recipient_type = 'responder'
                """,
                (action, alert_id, user_id),
            )

        return jsonify({
            "status": "ok",
            "notification_id": notif_id,
            "action": action,
            "payload": payload,
        }), 200


@responders_bp.route("/api/sos/<int:alert_id>/responders", methods=["GET"])
def get_sos_responders(alert_id: int):
    """
    List notified community responders and their status for an alert (alert owner only).
    Privacy rule: Only first name and approx distance are shown; never phone numbers or exact locations.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return _error("Authentication required. Please log in.", 401)

    with db_conn() as conn:
        alert = conn.execute("SELECT user_id FROM sos_alerts WHERE id = ?", (alert_id,)).fetchone()
        if not alert:
            return _error("SOS alert not found.", 404)
        if alert["user_id"] != user_id:
            return _error("Access denied: only the alert owner can view responders.", 403)

        # Query all notifications dispatched to responders for this alert
        rows = conn.execute(
            """
            SELECT sn.recipient_ref, sn.status, sn.created_at, u.name, n.payload_json
            FROM sos_notifications sn
            LEFT JOIN users u ON sn.recipient_ref = u.id
            LEFT JOIN notifications n ON sn.recipient_ref = n.user_id 
                 AND n.type = 'sos_responder_alert' 
                 AND json_extract(n.payload_json, '$.alert_id') = ?
            WHERE sn.alert_id = ? AND sn.recipient_type = 'responder'
            ORDER BY sn.id ASC
            """,
            (alert_id, alert_id),
        ).fetchall()

        responders = []
        for r in rows:
            name_raw = r["name"] or "Community Responder"
            first_name = name_raw.split()[0]
            status = r["status"]
            approx_dist = None

            if r["payload_json"]:
                try:
                    p = json.loads(r["payload_json"])
                    approx_dist = p.get("approx_distance_m")
                    if "status" in p:
                        status = p["status"]
                except Exception:
                    pass

            item = {
                "first_name": first_name,
                "status": status,
                "approx_distance_m": approx_dist,
            }

            # Privacy rule: Reveal exact location only to alert owner, and only after accepting 'on_my_way'
            if status == "on_my_way":
                resp_loc = conn.execute(
                    "SELECT lat, lon FROM responder_locations WHERE user_id = ?",
                    (r["recipient_ref"],),
                ).fetchone()
                if resp_loc:
                    item["lat"] = resp_loc["lat"]
                    item["lon"] = resp_loc["lon"]

            responders.append(item)

        return jsonify({
            "status": "ok",
            "alert_id": alert_id,
            "count": len(responders),
            "responders": responders,
        }), 200
