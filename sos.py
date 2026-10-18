"""
sos.py — Phase 3 Milestone 3: Core SOS Engine Blueprint for RakshaNet.

Endpoints:
- POST /api/sos/start         — Trigger an emergency SOS alert.
- POST /api/sos/<id>/location — Stream continuous location updates (1/s rate limit).
- POST /api/sos/<id>/resolve  — Resolve alert ("safe" | "false_alarm") and notify contacts.
- POST /api/sos/<id>/cancel   — Cancel within countdown + 10s window.
- GET  /api/sos/active        — Get current user's active SOS alert.
"""

import os
import re
import time
import secrets
import logging
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, session

import config
from config import (
    SOS_ENABLED,
    SOS_COUNTDOWN_SECONDS,
    SOS_COOLDOWN_SECONDS,
    SOS_MAX_PER_DAY,
    DEMO_MODE,
)
from db import db_conn
from notifier import send_sms, wa_link

logger = logging.getLogger(__name__)

sos_bp = Blueprint("sos", __name__)

# In-memory rate limiting for location updates: alert_id -> last_update_epoch (float)
_last_location_update = {}


# ── Blueprint-level Error Handlers & Guards ─────────────────────────────────

@sos_bp.before_request
def check_sos_enabled():
    """Ensure SOS features return 404 if SOS_ENABLED is False, except config check."""
    if request.path == "/api/sos/config":
        return None
    if not getattr(config, "SOS_ENABLED", True):
        return jsonify({"error": "Smart SOS feature is disabled."}), 404


@sos_bp.errorhandler(Exception)
def handle_sos_exception(e):
    """Blueprint-level JSON error handler ensuring uncaught SOS errors never crash the app."""
    logger.exception("[sos_bp] Unhandled exception in SOS engine: %s", str(e))
    return jsonify({"error": f"An error occurred in the SOS engine: {str(e)}"}), 500


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


def _get_user_info(user_id: str):
    """Retrieve user name and phone from database."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, name, phone FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Coverage & Street Snapping Helpers ──────────────────────────────────────

def _is_inside_bbox(lat: float, lon: float) -> bool:
    """Check if (lat, lon) is within the coverage bounding box."""
    import app as app_module
    bbox = getattr(app_module, "BBOX", None)
    if not bbox:
        try:
            from graph_loader import get_graph, get_bbox
            G = getattr(app_module, "G", None) or get_graph()
            bbox = get_bbox(G)
        except Exception:
            return False
    north, south, east, west = bbox
    return (south <= lat <= north and west <= lon <= east)


def _snap_nearest_street_name(lat: float, lon: float) -> str:
    """Snap coordinates to the nearest street name using OSMnx graph edges."""
    import app as app_module
    G = getattr(app_module, "G", None)
    if not G:
        return "Nearby street"
    try:
        import osmnx as ox
        u, v, k = ox.nearest_edges(G, lon, lat)
        data = G.get_edge_data(u, v, k) or {}
        raw = data.get("name")
        if raw:
            return raw[0] if isinstance(raw, list) else str(raw)
    except Exception as e:
        logger.warning("[sos_bp] Street snap fallback: %s", str(e))
    return "Nearby street"


def _parse_ts(ts_str: str) -> datetime:
    """Parse SQLite timestamp into datetime object."""
    if not ts_str:
        return datetime.utcnow()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return datetime.utcnow()


# ── SOS Core Endpoints ───────────────────────────────────────────────────────

@sos_bp.route("/api/sos/start", methods=["POST"])
def start_sos():
    """
    Initiate an emergency SOS alert.
    Requires login, validates coverage bbox, checks contacts, enforces cooldown & daily limit,
    generates tracking token, and notifies trusted contacts.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return _error("Authentication required. Please log in.", 401)

    user = _get_user_info(user_id)
    if not user:
        return _error("User not found.", 404)
    user_name = user.get("name") or "RakshaNet User"

    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["lat"])
        lon = float(data["lon"])
    except (KeyError, ValueError, TypeError):
        return _error("lat and lon are required numeric coordinates.", 400)

    # 1. Coverage area check (simulated locations allowed in DEMO_MODE if requested)
    is_simulated = bool(data.get("simulated") and getattr(config, "DEMO_MODE", False))
    if not is_simulated and not _is_inside_bbox(lat, lon):
        return _error(f"Location ({lat:.4f}, {lon:.4f}) is outside the RakshaNet coverage area.", 400)

    with db_conn() as conn:
        # 2. Check trusted contacts (must have at least 1 contact)
        contacts = conn.execute(
            "SELECT name, phone FROM trusted_contacts WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
        if not contacts:
            return _error("No trusted contacts found. Please add at least one trusted contact before triggering SOS.", 400)

        # 3. Check for existing active or countdown alert
        active_alert = conn.execute(
            """
            SELECT id, status, created_at, track_token
            FROM sos_alerts
            WHERE user_id = ? AND status IN ('active', 'countdown')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if active_alert:
            return jsonify({
                "error": "You already have an active SOS alert.",
                "active_alert_id": active_alert["id"],
                "status": active_alert["status"],
            }), 409

        # 4. Enforce Cooldown (SOS_COOLDOWN_SECONDS)
        cooldown_sec = getattr(config, "SOS_COOLDOWN_SECONDS", 30)
        last_alert = conn.execute(
            "SELECT created_at FROM sos_alerts WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if last_alert:
            last_dt = _parse_ts(last_alert["created_at"])
            elapsed = (datetime.utcnow() - last_dt).total_seconds()
            if elapsed < cooldown_sec:
                remaining = int(cooldown_sec - elapsed)
                return _error(f"SOS alert is on cooldown. Please wait {remaining} seconds before triggering another alert.", 429)

        # 5. Enforce Daily Limit (SOS_MAX_PER_DAY)
        daily_max = getattr(config, "SOS_MAX_PER_DAY", 5)
        day_count = conn.execute(
            """
            SELECT COUNT(*) FROM sos_alerts
            WHERE user_id = ? AND created_at >= datetime('now', '-24 hours')
            """,
            (user_id,),
        ).fetchone()[0]
        if day_count >= daily_max:
            return _error(f"Daily SOS limit reached (maximum {daily_max} alerts per 24 hours).", 429)

        # 6. Snap to nearest street name & generate tracking token
        street_name = _snap_nearest_street_name(lat, lon)
        track_token = secrets.token_urlsafe(16)
        is_countdown = bool(data.get("countdown", False))
        initial_status = "countdown" if is_countdown else "active"

        # 7. Create alert row in sos_alerts
        cursor = conn.execute(
            """
            INSERT INTO sos_alerts (
                user_id, status, start_lat, start_lon, last_lat, last_lon,
                street_name, track_token, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (user_id, initial_status, lat, lon, lat, lon, street_name, track_token),
        )
        alert_id = cursor.lastrowid

        # Record initial location point
        conn.execute(
            "INSERT INTO sos_locations (alert_id, lat, lon, created_at) VALUES (?, ?, ?, datetime('now'))",
            (alert_id, lat, lon),
        )

    # 8. Dispatch notifications if immediate (not in countdown) - done outside DB transaction
    notifications_sent = []
    if not is_countdown:
        maps_link = f"https://maps.google.com/?q={lat:.5f},{lon:.5f}"
        track_link = f"/track/{track_token}"
        sms_body = (
            f"EMERGENCY SOS: {user_name} needs urgent help near {street_name}! "
            f"Live track: {track_link} | Location: {maps_link}"
        )
        for c in contacts:
            sms_res = send_sms(
                to=c["phone"],
                body=sms_body,
                alert_id=alert_id,
                recipient_type="contact"
            )
            notifications_sent.append({
                "recipient_name": c["name"],
                "phone": sms_res["to_masked"],
                "status": sms_res["status"],
                "wa_link": wa_link(c["phone"], sms_body),
            })

        # Milestone 4: Notify nearby verified community responders
        try:
            from responders import notify_community_responders
            notify_community_responders(alert_id, lat, lon)
        except Exception as e:
            logger.exception("[sos_bp] Community responder notification failed: %s", str(e))

        return jsonify({
            "status": "ok",
            "notifications": notifications_sent,
            "alert": {
                "id": alert_id,
                "status": initial_status,
                "user_id": user_id,
                "street_name": street_name,
                "lat": lat,
                "lon": lon,
                "track_token": track_token,
                "track_url": f"/track/{track_token}",
                "notifications_count": len(notifications_sent),
                "notifications": notifications_sent,
            }
        }), 201


@sos_bp.route("/api/sos/<int:alert_id>/location", methods=["POST"])
def update_sos_location(alert_id: int):
    """
    Append continuous location point to the SOS alert (owner only).
    Rate-limited to 1 update per second.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return _error("Authentication required. Please log in.", 401)

    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["lat"])
        lon = float(data["lon"])
    except (KeyError, ValueError, TypeError):
        return _error("lat and lon are required numeric coordinates.", 400)

    with db_conn() as conn:
        alert = conn.execute("SELECT * FROM sos_alerts WHERE id = ?", (alert_id,)).fetchone()
        if not alert:
            return _error("SOS alert not found.", 404)
        if alert["user_id"] != user_id:
            return _error("Access denied: only the alert owner can update location.", 403)
        if alert["status"] not in ("active", "countdown"):
            return _error(f"Cannot update location for an alert with status '{alert['status']}'.", 400)

        # Rate limit: 1 location update per second
        now_ts = time.time()
        last_ts = _last_location_update.get(alert_id, 0.0)
        if (now_ts - last_ts) < 1.0:
            return _error("Location update rate limit exceeded (maximum 1 update per second).", 429)

        _last_location_update[alert_id] = now_ts

        # Append to sos_locations and update last_lat / last_lon in sos_alerts
        conn.execute(
            "INSERT INTO sos_locations (alert_id, lat, lon, created_at) VALUES (?, ?, ?, datetime('now'))",
            (alert_id, lat, lon),
        )
        conn.execute(
            "UPDATE sos_alerts SET last_lat = ?, last_lon = ? WHERE id = ?",
            (lat, lon, alert_id),
        )

        return jsonify({
            "status": "ok",
            "alert_id": alert_id,
            "last_lat": lat,
            "last_lon": lon,
        }), 200


@sos_bp.route("/api/sos/<int:alert_id>/resolve", methods=["POST"])
def resolve_sos(alert_id: int):
    """
    Resolve an active SOS alert (owner only).
    Body: {outcome: 'safe' | 'false_alarm'}
    Invalidates track_token and notifies contacts with an "I'm safe" follow-up message.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return _error("Authentication required. Please log in.", 401)

    data = request.get_json(silent=True) or {}
    outcome = data.get("outcome", "safe").strip().lower()
    if outcome not in ("safe", "false_alarm"):
        outcome = "safe"

    with db_conn() as conn:
        alert = conn.execute("SELECT * FROM sos_alerts WHERE id = ?", (alert_id,)).fetchone()
        if not alert:
            return _error("SOS alert not found.", 404)
        if alert["user_id"] != user_id:
            return _error("Access denied: only the alert owner can resolve.", 403)
        if alert["status"] in ("resolved", "cancelled"):
            return _error(f"Alert is already {alert['status']}.", 400)

        # Record token in ended_tracks before clearing so tracking link displays friendly status
        if alert["track_token"]:
            from track import record_ended_track
            record_ended_track(alert["track_token"], outcome, alert_id, conn=conn)

        # Set status, resolved_at, and invalidate track_token
        conn.execute(
            """
            UPDATE sos_alerts
            SET status = 'resolved', resolved_at = datetime('now'), track_token = NULL
            WHERE id = ?
            """,
            (alert_id,),
        )

        contacts = conn.execute(
            "SELECT name, phone FROM trusted_contacts WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()

    user = _get_user_info(user_id)
    user_name = user.get("name") if user else "RakshaNet User"
    safe_msg = f"RakshaNet SOS Update: {user_name} has resolved their alert as '{outcome}'. They are safe. Thank you."

    notifications_sent = []
    for c in contacts:
        sms_res = send_sms(
            to=c["phone"],
            body=safe_msg,
            alert_id=alert_id,
            recipient_type="contact"
        )
        notifications_sent.append({
            "recipient_name": c["name"],
            "phone": sms_res["to_masked"],
            "status": sms_res["status"],
        })

    return jsonify({
            "status": "ok",
            "alert_id": alert_id,
            "outcome": outcome,
            "resolved_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "token_invalidated": True,
            "notifications": notifications_sent,
        }), 200


@sos_bp.route("/api/sos/<int:alert_id>/cancel", methods=["POST"])
def cancel_sos(alert_id: int):
    """
    Cancel an SOS alert (owner only).
    Allowed only within SOS_COUNTDOWN_SECONDS + 10 s of creation.
    If cancelled during countdown, no notification is sent.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return _error("Authentication required. Please log in.", 401)

    with db_conn() as conn:
        alert = conn.execute("SELECT * FROM sos_alerts WHERE id = ?", (alert_id,)).fetchone()
        if not alert:
            return _error("SOS alert not found.", 404)
        if alert["user_id"] != user_id:
            return _error("Access denied: only the alert owner can cancel.", 403)
        if alert["status"] in ("resolved", "cancelled"):
            return _error(f"Alert is already {alert['status']}.", 400)

        # Check cancellation window: SOS_COUNTDOWN_SECONDS + 10s
        countdown_sec = getattr(config, "SOS_COUNTDOWN_SECONDS", 5)
        max_cancel_window = countdown_sec + 10.0
        created_dt = _parse_ts(alert["created_at"])
        elapsed = (datetime.utcnow() - created_dt).total_seconds()

        if elapsed > max_cancel_window:
            return _error(
                f"Cancellation window expired ({int(max_cancel_window)}s limit). Please use /resolve instead.",
                400,
            )

        was_countdown = (alert["status"] == "countdown" or elapsed <= countdown_sec)

        # Record token in ended_tracks before clearing
        if alert["track_token"]:
            from track import record_ended_track
            record_ended_track(alert["track_token"], "cancelled", alert_id, conn=conn)

        # Update status to cancelled and invalidate track_token
        conn.execute(
            """
            UPDATE sos_alerts
            SET status = 'cancelled', resolved_at = datetime('now'), track_token = NULL
            WHERE id = ?
            """,
            (alert_id,),
        )

        return jsonify({
            "status": "ok",
            "alert_id": alert_id,
            "status": "cancelled",
            "cancelled_during_countdown": was_countdown,
            "notifications_sent": False if was_countdown else True,
        }), 200


@sos_bp.route("/api/sos/active", methods=["GET"])
def get_active_sos():
    """
    Return current user's active or countdown alert, if any.
    Includes recorded location trail.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return _error("Authentication required. Please log in.", 401)

    with db_conn() as conn:
        alert = conn.execute(
            """
            SELECT * FROM sos_alerts
            WHERE user_id = ? AND status IN ('active', 'countdown')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if not alert:
            return jsonify({"active": False, "alert": None}), 200

        locations = conn.execute(
            "SELECT lat, lon, created_at FROM sos_locations WHERE alert_id = ? ORDER BY id",
            (alert["id"],),
        ).fetchall()

        alert_dict = dict(alert)
        alert_dict["locations"] = [dict(loc) for loc in locations]

        return jsonify({
            "active": True,
            "alert": alert_dict,
        }), 200


@sos_bp.route("/api/sos/config", methods=["GET"])
def get_sos_config():
    """
    Public configuration endpoint for SOS feature frontend.
    Returns feature flags, countdown and polling intervals.
    """
    return jsonify({
        "sos_enabled": bool(getattr(config, "SOS_ENABLED", True)),
        "demo_mode": bool(getattr(config, "DEMO_MODE", False)),
        "countdown_seconds": int(getattr(config, "SOS_COUNTDOWN_SECONDS", 5)),
        "location_update_seconds": int(getattr(config, "SOS_LOCATION_UPDATE_SECONDS", 5)),
        "responder_radius_m": int(getattr(config, "RESPONDER_RADIUS_M", 800)),
        "max_contacts": int(getattr(config, "SOS_MAX_CONTACTS", 3)),
    }), 200

