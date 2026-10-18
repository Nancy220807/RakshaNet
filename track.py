"""
track.py — Public Live SOS Tracking Page and API Blueprint (RakshaNet Phase 3 Milestone 5).

Provides:
- GET /track/<token>      : Public, mobile-friendly Leaflet tracking page (no login required).
- GET /api/track/<token>  : JSON tracking endpoint polling current coordinates, trail, and street name.

Security and Privacy:
- Never exposes user_id, phone, email, contacts, or personal details.
- In-memory rate limiting per client IP (60 requests / minute).
- Cache-Control: no-store on all responses to prevent caching sensitive live locations.
- Token hashing via SHA-256 for ended tracks: preserves privacy and security without token enumeration.
"""

import os
import time
import hashlib
import logging
import threading
from datetime import datetime
from collections import defaultdict
from flask import Blueprint, request, jsonify, render_template

import config
from db import db_conn

logger = logging.getLogger(__name__)

track_bp = Blueprint("track_bp", __name__, template_folder="templates")

# ── In-Memory IP Rate Limiting ───────────────────────────────────────────────
RATE_LIMIT_WINDOW = 60    # seconds
RATE_LIMIT_MAX    = 60    # maximum requests per window
_rate_limit_lock  = threading.Lock()
_rate_limit_store = defaultdict(list)


def _check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if limit exceeded."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    with _rate_limit_lock:
        records = _rate_limit_store[ip]
        records = [t for t in records if t > cutoff]
        if len(records) >= RATE_LIMIT_MAX:
            _rate_limit_store[ip] = records
            return False
        records.append(now)
        _rate_limit_store[ip] = records
        return True


def reset_rate_limits():
    """Reset rate limiting store (useful for tests)."""
    with _rate_limit_lock:
        _rate_limit_store.clear()


def _get_client_ip() -> str:
    """Extract client IP, handling proxies (X-Forwarded-For)."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


# ── Security & Header Hooks ──────────────────────────────────────────────────
@track_bp.before_request
def check_sos_feature():
    """Reject requests if SOS feature is disabled."""
    if not getattr(config, "SOS_ENABLED", True):
        return jsonify({"error": "Smart SOS feature is disabled.", "status": "disabled"}), 404


@track_bp.after_request
def add_security_headers(response):
    """Enforce no-store caching on all live tracking responses."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


# ── Ended Tracks Helpers ─────────────────────────────────────────────────────
def hash_token(token: str) -> str:
    """Compute deterministic SHA-256 hex digest of a tracking token."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def record_ended_track(token: str, outcome: str, alert_id: int = None, conn=None) -> None:
    """
    Store an invalidated token's hash and outcome in ended_tracks.
    This allows visitors with the link to see a friendly ended/cancelled message
    without leaking active tokens or enabling enumeration.
    """
    if not token:
        return
    token_h = hash_token(token)
    sql = """
    INSERT OR REPLACE INTO ended_tracks (token_hash, alert_id, outcome, ended_at)
    VALUES (?, ?, ?, datetime('now'))
    """
    if conn is not None:
        conn.execute(sql, (token_h, alert_id, outcome))
    else:
        with db_conn() as c:
            c.execute(sql, (token_h, alert_id, outcome))


def _parse_ts(ts_val) -> datetime:
    """Parse SQLite timestamp string into datetime."""
    if not ts_val:
        return datetime.utcnow()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(str(ts_val).strip(), fmt)
        except ValueError:
            pass
    return datetime.utcnow()


def _get_track_status(token: str):
    """
    Look up the token across active alerts and ended tracks.
    Returns None if unknown token.
    Returns dict with {status, last_lat, last_lon, trail, street_name, updated_at, message}.
    NEVER includes PII (no phone, no email, no user_id).
    """
    if not token or not token.strip():
        return None

    token_clean = token.strip()
    token_h = hash_token(token_clean)

    with db_conn() as conn:
        # 1. Check active sos_alerts by live token
        alert = conn.execute(
            """
            SELECT id, status, start_lat, start_lon, last_lat, last_lon,
                   street_name, created_at, resolved_at
            FROM sos_alerts
            WHERE track_token = ?
            ORDER BY id DESC LIMIT 1
            """,
            (token_clean,),
        ).fetchone()

        if alert:
            alert_id = alert["id"]
            status = alert["status"]

            # Fetch trail points
            trail_rows = conn.execute(
                "SELECT lat, lon, created_at FROM sos_locations WHERE alert_id = ? ORDER BY id ASC",
                (alert_id,),
            ).fetchall()
            trail = [[r["lat"], r["lon"]] for r in trail_rows]
            last_lat = alert["last_lat"] if alert["last_lat"] is not None else alert["start_lat"]
            last_lon = alert["last_lon"] if alert["last_lon"] is not None else alert["start_lon"]
            street_name = alert["street_name"] or ""
            updated_at = trail_rows[-1]["created_at"] if trail_rows else alert["created_at"]

            # Check expiration against TRACK_LINK_TTL_HOURS
            ttl_hours = getattr(config, "TRACK_LINK_TTL_HOURS", 6)
            created_dt = _parse_ts(alert["created_at"])
            elapsed_sec = (datetime.utcnow() - created_dt).total_seconds()
            if elapsed_sec > ttl_hours * 3600:
                return {
                    "status": "expired",
                    "message": "This link has expired",
                    "last_lat": last_lat,
                    "last_lon": last_lon,
                    "trail": trail,
                    "street_name": street_name,
                    "updated_at": updated_at,
                }

            # Check if resolved or cancelled (if token wasn't cleared yet)
            if status == "cancelled":
                return {
                    "status": "cancelled",
                    "message": "This alert was cancelled",
                    "last_lat": last_lat,
                    "last_lon": last_lon,
                    "trail": trail,
                    "street_name": street_name,
                    "updated_at": alert["resolved_at"] or updated_at,
                }
            if status == "resolved":
                return {
                    "status": "ended",
                    "message": "This alert has ended. The person has marked themselves safe.",
                    "last_lat": last_lat,
                    "last_lon": last_lon,
                    "trail": trail,
                    "street_name": street_name,
                    "updated_at": alert["resolved_at"] or updated_at,
                }

            # Active tracking
            return {
                "status": "active",
                "last_lat": last_lat,
                "last_lon": last_lon,
                "trail": trail,
                "street_name": street_name,
                "updated_at": updated_at,
            }

        # 2. Check ended_tracks table by SHA-256 token hash
        ended = conn.execute(
            "SELECT token_hash, alert_id, outcome, ended_at FROM ended_tracks WHERE token_hash = ?",
            (token_h,),
        ).fetchone()

        if ended:
            outcome = ended["outcome"]
            alert_id = ended["alert_id"]
            last_lat = None
            last_lon = None
            trail = []
            street_name = ""
            updated_at = ended["ended_at"]

            # Retrieve final location & trail if alert record exists
            if alert_id:
                alert_row = conn.execute(
                    "SELECT last_lat, last_lon, start_lat, start_lon, street_name, resolved_at FROM sos_alerts WHERE id = ?",
                    (alert_id,),
                ).fetchone()
                if alert_row:
                    last_lat = alert_row["last_lat"] if alert_row["last_lat"] is not None else alert_row["start_lat"]
                    last_lon = alert_row["last_lon"] if alert_row["last_lon"] is not None else alert_row["start_lon"]
                    street_name = alert_row["street_name"] or ""
                    updated_at = alert_row["resolved_at"] or updated_at

                loc_rows = conn.execute(
                    "SELECT lat, lon FROM sos_locations WHERE alert_id = ? ORDER BY id ASC",
                    (alert_id,),
                ).fetchall()
                trail = [[r["lat"], r["lon"]] for r in loc_rows]

            if outcome == "cancelled":
                return {
                    "status": "cancelled",
                    "message": "This alert was cancelled",
                    "last_lat": last_lat,
                    "last_lon": last_lon,
                    "trail": trail,
                    "street_name": street_name,
                    "updated_at": updated_at,
                }
            else:
                return {
                    "status": "ended",
                    "message": "This alert has ended. The person has marked themselves safe.",
                    "last_lat": last_lat,
                    "last_lon": last_lon,
                    "trail": trail,
                    "street_name": street_name,
                    "updated_at": updated_at,
                }

    # 3. Not found anywhere
    return None


# ── Blueprint Routes ─────────────────────────────────────────────────────────

@track_bp.route("/track/<token>", methods=["GET"])
def view_track(token: str):
    """
    Public live tracking page.
    Renders dark-themed mobile-friendly Leaflet map.
    Returns 404 with friendly state if token is unknown.
    """
    poll_sec = getattr(config, "SOS_LOCATION_UPDATE_SECONDS", 5)
    status_data = _get_track_status(token)

    if status_data is None:
        return render_template(
            "track.html",
            token=token,
            status="not_found",
            message="Link not found",
            initial_data=None,
            poll_interval=poll_sec,
        ), 404

    return render_template(
        "track.html",
        token=token,
        status=status_data["status"],
        message=status_data.get("message", ""),
        initial_data=status_data,
        poll_interval=poll_sec,
    ), 200


@track_bp.route("/api/track/<token>", methods=["GET"])
def api_track(token: str):
    """
    Public live tracking API endpoint.
    Returns {status, last_lat, last_lon, trail, street_name, updated_at}.
    Rate-limited per client IP.
    """
    client_ip = _get_client_ip()
    if not _check_rate_limit(client_ip):
        return jsonify({
            "error": "Rate limit exceeded. Maximum 60 requests per minute.",
            "status": "rate_limited",
        }), 429

    status_data = _get_track_status(token)
    if status_data is None:
        return jsonify({
            "error": "Link not found",
            "status": "not_found",
        }), 404

    return jsonify(status_data), 200
