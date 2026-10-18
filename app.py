"""
app.py — RakshaNet Flask application.

Startup sequence:
  1. Load / download OSM graph (graph_loader).
  2. Seed DB if empty (seed_data).
  3. Precompute safety scores for all 24 hours (scoring).
  4. Serve the API and static frontend.

Run:
    python app.py
"""

import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta
from uuid import uuid4

import requests as _requests

from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

from config import (
    CENTER_LAT, CENTER_LON, RADIUS_M,
    SAMPLE_START, SAMPLE_END,
    DATA_DIR, DB_PATH,
    DEMO_MODE, DEMO_ROUTES, VERIFY_THRESHOLD, VOTE_RADIUS_M,
    SOS_ENABLED,
)
from graph_loader import get_graph, get_bbox
from scoring import (
    precompute_scores, build_segment_list, score_to_band,
    update_incremental_scores, _parse_ts,
)
from routing import snap_to_node, compute_routes
from db import init_db, db_conn, load_edge_attrs, DEMO_USERS, DEMO_PASSWORD

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Flask setup ───────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app.secret_key = "rakshanet_secret_demo_key_2026"
CORS(app)   # allow the frontend (same host, different port in dev) to call the API

if SOS_ENABLED:
    from contacts import contacts_bp
    app.register_blueprint(contacts_bp)
    from notifier import notifier_bp
    app.register_blueprint(notifier_bp)
    from sos import sos_bp
    app.register_blueprint(sos_bp)
    from responders import responders_bp
    app.register_blueprint(responders_bp)
    from track import track_bp
    app.register_blueprint(track_bp)

# ── App-level state (populated at startup) ────────────────────────────────────
G            = None     # OSMnx graph
HOUR_SCORES  = None     # dict[hour -> dict[(u,v,key) -> score]]
BBOX         = None     # (north, south, east, west)
STREET_INDEX = {}       # lowercase_name -> {name, lat, lon} — built from graph at startup

# ── Nominatim rate limiter (1 req / s, in-memory query cache) ─────────────────
_nom_lock      = threading.Lock()
_nom_last_call = 0.0
_nom_cache: dict = {}   # query_lower -> list[result]


# ── Startup ───────────────────────────────────────────────────────────────────

# ── Street-index helpers ─────────────────────────────────────────────────────

def build_street_index(graph) -> dict:
    """
    Build a case-insensitive name index from the graph's edge `name` attributes.
    For each unique street name keep the midpoint of the longest edge.
    Returns dict: lowercase_name -> {name, lat, lon}.
    """
    idx: dict = {}
    for u, v, key, data in graph.edges(keys=True, data=True):
        raw_name = data.get("name")
        if not raw_name:
            continue
        names = raw_name if isinstance(raw_name, list) else [raw_name]
        length = data.get("length", 0.0)

        # Midpoint of the edge
        if "geometry" in data:
            coords   = list(data["geometry"].coords)
            mid      = coords[len(coords) // 2]
            mid_lon, mid_lat = mid[0], mid[1]
        else:
            mid_lat = (graph.nodes[u]["y"] + graph.nodes[v]["y"]) / 2
            mid_lon = (graph.nodes[u]["x"] + graph.nodes[v]["x"]) / 2

        for name in names:
            name = str(name).strip()
            if not name:
                continue
            key_lower = name.lower()
            if key_lower not in idx or length > idx[key_lower]["length"]:
                idx[key_lower] = {"name": name, "lat": mid_lat, "lon": mid_lon, "length": length}

    logger.info("Street index built: %d unique names.", len(idx))
    return idx


def _search_local(query: str, limit: int = 6) -> list:
    """Prefix-first, then substring matches against the in-memory street index."""
    q = query.lower().strip()
    prefix, substr = [], []
    for name_lower, info in STREET_INDEX.items():
        if name_lower.startswith(q):
            prefix.append(info)
        elif q in name_lower:
            substr.append(info)
    results = []
    for info in (prefix + substr)[:limit]:
        results.append({"label": info["name"], "lat": info["lat"], "lon": info["lon"], "source": "street"})
    return results


def _search_nominatim(query: str, limit: int = 5) -> list:
    """Call Nominatim bounded to the app bbox. Rate-limited to 1 req/s. Fails silently."""
    global _nom_last_call
    if len(query) < 3 or BBOX is None:
        return []
    cache_key = query.lower().strip()
    if cache_key in _nom_cache:
        return _nom_cache[cache_key]

    # Enforce 1-req/s globally
    with _nom_lock:
        elapsed = time.time() - _nom_last_call
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _nom_last_call = time.time()

    north, south, east, west = BBOX
    try:
        resp = _requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "format":  "jsonv2",
                "limit":   limit,
                "viewbox": f"{west},{north},{east},{south}",
                "bounded": 1,
                "q":       query,
            },
            headers={"User-Agent": "RakshaNet-hackathon/1.0"},
            timeout=3,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("Nominatim failed for %r: %s", query, exc)
        _nom_cache[cache_key] = []
        return []

    results = []
    for item in data:
        label = item.get("display_name", "").split(",")[0].strip()
        results.append({
            "label":  label,
            "lat":    float(item["lat"]),
            "lon":    float(item["lon"]),
            "source": "place",
        })
    _nom_cache[cache_key] = results
    return results


_LATLON_RE = re.compile(r"^\s*(-?\d{1,3}\.?\d*)\s*[,\s]\s*(-?\d{1,3}\.?\d*)\s*$")

def _parse_latlon(query: str):
    """Return (lat, lon) if query looks like a pasted coordinate pair, else None."""
    m = _LATLON_RE.match(query)
    if not m:
        return None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None


def startup():
    global G, HOUR_SCORES, BBOX, STREET_INDEX

    os.makedirs(DATA_DIR, exist_ok=True)

    # 1. Database
    init_db()

    # 2. Graph
    G    = get_graph()
    BBOX = get_bbox(G)

    # 3. Seed if edge_attrs table is empty
    with db_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM edge_attrs").fetchone()[0]

    if count == 0:
        logger.info("Edge attrs table is empty — running seed_data …")
        from seed_data import run_seed
        run_seed()
    else:
        logger.info("Seed data already present (%d edge rows).", count)

    # 4. Precompute scores
    HOUR_SCORES = precompute_scores(G)

    # 5. Build street-name search index
    STREET_INDEX = build_street_index(G)

    logger.info("RakshaNet startup complete. Ready to serve.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_hour(raw) -> int:
    """Parse hour param: integer 0-23, or 'now' -> current local hour."""
    if raw is None or str(raw).lower() == "now":
        return datetime.now().hour
    try:
        h = int(raw)
        if not 0 <= h <= 23:
            raise ValueError
        return h
    except (ValueError, TypeError):
        return datetime.now().hour


def _error(msg: str, status: int = 400):
    return jsonify({"error": msg}), status


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the single-page frontend."""
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/meta")
def api_meta():
    """Return area metadata for the frontend to initialise the map."""
    north, south, east, west = BBOX
    return jsonify({
        "center":  {"lat": CENTER_LAT, "lon": CENTER_LON},
        "radius_m": RADIUS_M,
        "bbox":    {"north": north, "south": south, "east": east, "west": west},
        "sample_start": {"lat": SAMPLE_START[0], "lon": SAMPLE_START[1]},
        "sample_end":   {"lat": SAMPLE_END[0],   "lon": SAMPLE_END[1]},
        "demo_routes": DEMO_ROUTES,
        "demo_mode": DEMO_MODE,
        "verify_threshold": VERIFY_THRESHOLD,
        "vote_radius_m": VOTE_RADIUS_M,
        "hour_options": [
            {"label": "Right now", "value": "now"},
            {"label": "6 PM",  "value": 18},
            {"label": "9 PM",  "value": 21},
            {"label": "11 PM", "value": 23},
        ],
    })


@app.route("/api/route")
def api_route():
    """
    GET /api/route?start_lat=&start_lon=&end_lat=&end_lon=&hour=

    Returns shortest and safest routes with per-segment score/color data.
    """
    try:
        start_lat = float(request.args["start_lat"])
        start_lon = float(request.args["start_lon"])
        end_lat   = float(request.args["end_lat"])
        end_lon   = float(request.args["end_lon"])
    except (KeyError, ValueError):
        return _error("start_lat, start_lon, end_lat, end_lon are required float parameters.")

    hour = _parse_hour(request.args.get("hour"))

    try:
        origin_node = snap_to_node(G, start_lat, start_lon)
        dest_node   = snap_to_node(G, end_lat,   end_lon)
    except ValueError as e:
        return _error(str(e))

    if origin_node == dest_node:
        return _error("Start and destination are the same point.")

    shortest, safest, err = compute_routes(G, origin_node, dest_node, hour, HOUR_SCORES)

    if err:
        return _error(err)

    return jsonify({
        "hour":     hour,
        "shortest": shortest,
        "safest":   safest,
    })


@app.route("/api/segments")
def api_segments():
    """
    GET /api/segments?hour=

    Return all road segments with their score and color for the heatmap overlay.
    This can be a large payload; the frontend should cache it per hour.
    """
    hour     = _parse_hour(request.args.get("hour"))
    segments = build_segment_list(G, HOUR_SCORES, hour)
    return jsonify({"hour": hour, "segments": segments})


@app.route("/api/search")
def api_search():
    """
    GET /api/search?q=<text>
    Returns up to 6 location results: local street names (instant, offline)
    merged with Nominatim place search (live, supplementary, bounded to bbox).
    Also accepts a pasted "lat, lon" string.
    Always returns 200 with {results: [...]}; never 500.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []})

    # 1. Lat/lon paste?
    latlon = _parse_latlon(q)
    if latlon:
        lat, lon = latlon
        return jsonify({"results": [{"label": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon, "source": "place"}]})

    # 2. Local street names (instant)
    local = _search_local(q, limit=6)

    # 3. Nominatim (live, only if query is 3+ chars)
    remote = _search_nominatim(q, limit=5) if len(q) >= 3 else []

    # 4. Merge: local first, then dedupe by label
    seen: set = {r["label"].lower() for r in local}
    for r in remote:
        if r["label"].lower() not in seen:
            local.append(r)
            seen.add(r["label"].lower())

    return jsonify({"results": local[:6]})


@app.route("/api/nearest")
def api_nearest():
    """
    GET /api/nearest?lat=&lon=
    Returns the street name of the nearest graph edge to the given point,
    along with snapped coordinates and area coverage flag.
    """
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, ValueError):
        return _error("lat and lon are required.")

    north, south, east, west = BBOX
    in_area = (south <= lat <= north) and (west <= lon <= east)

    try:
        import osmnx as ox
        u, v, ekey = ox.nearest_edges(G, lon, lat)
        data = G.get_edge_data(u, v, ekey) or {}
        raw  = data.get("name")
        name = (raw[0] if isinstance(raw, list) else raw) if raw else None
        
        # Snapped lat/lon: midpoint of edge
        if "geometry" in data:
            coords = list(data["geometry"].coords)
            mid = coords[len(coords) // 2]
            snapped_lon, snapped_lat = mid[0], mid[1]
        else:
            snapped_lat = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
            snapped_lon = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2

        display_name = name or "Dropped pin"
        return jsonify({
            "street_name": display_name,
            "name": display_name,
            "edge_id": f"{u}_{v}_{ekey}",
            "edge_u": int(u),
            "edge_v": int(v),
            "edge_key": int(ekey),
            "snapped_lat": round(snapped_lat, 6),
            "snapped_lon": round(snapped_lon, 6),
            "in_area": in_area,
        })
    except Exception:
        return jsonify({
            "street_name": "Dropped pin",
            "name": "Dropped pin",
            "edge_id": None,
            "snapped_lat": lat,
            "snapped_lon": lon,
            "in_area": in_area,
        })


# ── Haversine helper ──────────────────────────────────────────────────────────

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


# ── Auth helpers ──────────────────────────────────────────────────────────────

def get_current_user():
    user_id = session.get("user_id")
    if not user_id and DEMO_MODE:
        user_id = "user_asha"
        session["user_id"] = user_id
    if not user_id:
        return None
    with db_conn() as conn:
        row = conn.execute("SELECT id, name, email, phone FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def require_user():
    user = get_current_user()
    if not user:
        return None, _error("Authentication required. Please log in.", 401)
    return user, None


def _get_street_name_for_edge(u, v, key=0):
    if not G:
        return "Nearby street"
    try:
        data = G.get_edge_data(u, v, key) or {}
        raw = data.get("name")
        if raw:
            return raw[0] if isinstance(raw, list) else str(raw)
    except Exception:
        pass
    return "Nearby street"


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.route("/api/auth/me")
def api_auth_me():
    user = get_current_user()
    return jsonify({
        "authenticated": user is not None,
        "user": user,
        "demo_mode": DEMO_MODE,
        "demo_users": DEMO_USERS if DEMO_MODE else [],
    })


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or data.get("email") or data.get("phone") or data.get("username") or "").strip()
    password = data.get("password") or ""
    if not identifier or not password:
        return _error("Identifier and password are required.", 400)

    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? OR phone = ? OR id = ? OR name = ?",
            (identifier, identifier, identifier, identifier),
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return _error("Invalid email/phone or password.", 401)

        session["user_id"] = row["id"]
        return jsonify({
            "status": "ok",
            "user": {
                "id": row["id"],
                "name": row["name"],
                "email": row["email"],
                "phone": row["phone"],
            }
        })


@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    identifier = (data.get("identifier") or data.get("email") or data.get("phone") or "").strip()
    password = data.get("password") or ""

    if not name or not identifier or not password:
        return _error("Name, phone/email, and password are required.", 400)
    if len(password) < 4:
        return _error("Password must be at least 4 characters.", 400)

    is_email = "@" in identifier
    email = identifier if is_email else None
    phone = identifier if not is_email else None

    with db_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE (email IS NOT NULL AND email = ?) OR (phone IS NOT NULL AND phone = ?)",
            (identifier, identifier),
        ).fetchone()
        if existing:
            return _error("An account with this email/phone already exists.", 409)

        new_id = f"user_{uuid4().hex[:8]}"
        pw_hash = generate_password_hash(password)
        conn.execute(
            "INSERT INTO users (id, name, email, phone, password_hash) VALUES (?, ?, ?, ?, ?)",
            (new_id, name, email, phone, pw_hash),
        )
        session["user_id"] = new_id
        return jsonify({
            "status": "ok",
            "user": {"id": new_id, "name": name, "email": email, "phone": phone}
        }), 201


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.pop("user_id", None)
    return jsonify({"status": "ok"})


@app.route("/api/auth/switch", methods=["POST"])
def api_auth_switch():
    if not DEMO_MODE:
        return _error("User switching is only permitted in DEMO_MODE.", 403)
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return _error("user_id is required.", 400)
    with db_conn() as conn:
        row = conn.execute("SELECT id, name, email, phone FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return _error("User not found.", 404)
        session["user_id"] = row["id"]
        return jsonify({"status": "ok", "user": dict(row)})


# ── Incident Reporting & Feed Endpoints ────────────────────────────────────────

@app.route("/api/incidents", methods=["GET", "POST"])
def api_incidents():
    if request.method == "POST":
        user, err_resp = require_user()
        if err_resp:
            return err_resp

        data = request.get_json(silent=True) or {}
        try:
            lat = float(data["lat"])
            lon = float(data["lon"])
        except (KeyError, ValueError, TypeError):
            return _error("lat and lon are required numeric values.", 400)

        # 1. Coverage area check
        north, south, east, west = BBOX
        if not (south <= lat <= north and west <= lon <= east):
            return _error(f"Incident location ({lat:.4f}, {lon:.4f}) is outside the RakshaNet coverage area.", 400)

        # 2. Type validation
        itype = str(data.get("type", "other")).strip().lower()
        if itype not in ("harassment", "poor_lighting", "unsafe_area", "other"):
            itype = "other"

        # 3. Time validation
        incident_time_str = data.get("incident_time")
        now = datetime.now()
        if incident_time_str:
            try:
                inc_dt = _parse_ts(incident_time_str)
                if inc_dt > now + timedelta(minutes=5):
                    return _error("Incident time cannot be in the future.", 400)
                if inc_dt < now - timedelta(days=30):
                    return _error("Incident time cannot be older than 30 days.", 400)
                formatted_inc_time = inc_dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                formatted_inc_time = now.strftime("%Y-%m-%d %H:%M:%S")
        else:
            formatted_inc_time = now.strftime("%Y-%m-%d %H:%M:%S")

        # 4. Note sanitization (max 200 chars)
        note = (data.get("note") or "").strip()
        note = re.sub(r"[<>]", "", note)[:200]

        with db_conn() as conn:
            # 5. Anti-abuse: max 5 reports per user per hour
            recent_count = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE reporter_id = ? AND reported_at >= datetime('now', '-1 hour')",
                (user["id"],),
            ).fetchone()[0]
            if recent_count >= 5:
                return _error("Rate limit exceeded: maximum 5 reports per hour.", 429)

            # 6. Anti-abuse: duplicate within 50 m in last 2 hours
            recent_similar = conn.execute(
                """
                SELECT id, lat, lon, itype, status, upvotes, reported_at, note
                FROM incidents
                WHERE itype = ?
                  AND status != 'rejected'
                  AND reported_at >= datetime('now', '-2 hours')
                """,
                (itype,),
            ).fetchall()

            for existing in recent_similar:
                dist = haversine_m(lat, lon, existing["lat"], existing["lon"])
                if dist <= 50.0:
                    return jsonify({
                        "error": "A report of this type already exists nearby (within 50 m) from the last 2 hours.",
                        "duplicate_incident_id": existing["id"],
                        "distance_m": round(dist, 1),
                        "existing_incident": dict(existing),
                    }), 409

            # 7. Snap to nearest edge
            import osmnx as ox
            try:
                u, v, ekey = ox.nearest_edges(G, lon, lat)
                edge_u, edge_v, edge_key = int(u), int(v), int(ekey)
                edge_id = f"{edge_u}_{edge_v}_{edge_key}"
            except Exception:
                edge_u, edge_v, edge_key, edge_id = None, None, 0, None

            # 8. Save pending incident (reporter counts as first confirmation)
            cursor = conn.execute(
                """
                INSERT INTO incidents (
                    lat, lon, edge_u, edge_v, edge_key, edge_id, itype, status,
                    upvotes, reporter_id, incident_time, note, description, reported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?, ?, datetime('now'))
                """,
                (lat, lon, edge_u, edge_v, edge_key, edge_id, itype, user["id"], formatted_inc_time, note, note),
            )
            incident_id = cursor.lastrowid

        street_name = _get_street_name_for_edge(edge_u, edge_v, edge_key) if edge_u is not None else "Nearby street"
        return jsonify({
            "status": "ok",
            "message": f"Incident reported successfully. Needs {VERIFY_THRESHOLD - 1} more community confirmation(s) to go live.",
            "incident": {
                "id": incident_id,
                "lat": lat,
                "lon": lon,
                "type": itype,
                "itype": itype,
                "street_name": street_name,
                "edge_id": edge_id,
                "edge_u": edge_u,
                "edge_v": edge_v,
                "edge_key": edge_key,
                "status": "pending",
                "upvotes": 1,
                "confirmations": 1,
                "confirmations_needed": VERIFY_THRESHOLD,
                "reporter_id": user["id"],
                "incident_time": formatted_inc_time,
                "note": note,
            }
        }), 201

    # GET /api/incidents
    status_filter = request.args.get("status", "verified")
    hours_filter  = request.args.get("hours")
    lat_q         = request.args.get("lat")
    lon_q         = request.args.get("lon")
    radius_q      = request.args.get("radius")
    itype_filter  = request.args.get("type")

    with db_conn() as conn:
        query = "SELECT * FROM incidents WHERE status = ?"
        params = [status_filter]

        if itype_filter:
            query += " AND itype = ?"
            params.append(itype_filter)

        if hours_filter:
            try:
                h = int(hours_filter)
                query += " AND reported_at >= datetime('now', ?)"
                params.append(f"-{h} hours")
            except ValueError:
                pass

        query += " ORDER BY reported_at DESC"
        rows = conn.execute(query, params).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        d["type"] = d.get("itype")
        d["street_name"] = _get_street_name_for_edge(d.get("edge_u"), d.get("edge_v"), d.get("edge_key", 0))
        d["confirmations"] = d.get("upvotes", 1)
        d["confirmations_needed"] = VERIFY_THRESHOLD

        if lat_q and lon_q:
            try:
                dist = haversine_m(float(lat_q), float(lon_q), d["lat"], d["lon"])
                d["distance_m"] = round(dist, 1)
                if radius_q and dist > float(radius_q):
                    continue
            except (ValueError, TypeError):
                pass
        items.append(d)

    return jsonify({"incidents": items, "count": len(items)})


@app.route("/api/incidents/<int:incident_id>/vote", methods=["POST"])
def api_incident_vote(incident_id):
    user, err_resp = require_user()
    if err_resp:
        return err_resp

    data = request.get_json(silent=True) or {}
    voter_lat = data.get("lat")
    voter_lon = data.get("lon")
    simulated = bool(data.get("simulated", False)) and DEMO_MODE

    with db_conn() as conn:
        inc = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if not inc:
            return _error("Incident not found.", 404)
        inc = dict(inc)

        # Rule 1: reporter cannot vote on own report
        if inc.get("reporter_id") and inc["reporter_id"] == user["id"]:
            return _error("You cannot confirm your own incident report.", 400)

        # Rule 2: one vote per user per incident
        existing_vote = conn.execute(
            "SELECT id FROM incident_votes WHERE incident_id = ? AND user_id = ?",
            (incident_id, user["id"]),
        ).fetchone()
        if existing_vote:
            return _error("You have already confirmed this incident.", 400)

        # Rule 3: location check within VOTE_RADIUS_M
        distance_m = 0.0
        if voter_lat is not None and voter_lon is not None:
            try:
                distance_m = haversine_m(float(voter_lat), float(voter_lon), inc["lat"], inc["lon"])
            except Exception:
                distance_m = 0.0

        if not simulated and distance_m > VOTE_RADIUS_M:
            return _error(
                f"You are too far from this incident to verify it ({int(distance_m)}m away, maximum allowed is {VOTE_RADIUS_M}m).",
                400,
            )

        # Insert vote
        conn.execute(
            """
            INSERT INTO incident_votes (incident_id, user_id, voter_lat, voter_lon, distance_m, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (incident_id, user["id"], voter_lat, voter_lon, round(distance_m, 1)),
        )

        vote_count = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM incident_votes WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()[0]
        total_confirmations = 1 + vote_count

        status = inc["status"]
        newly_verified = False
        verified_at = inc.get("verified_at")

        if total_confirmations >= VERIFY_THRESHOLD and status == "pending":
            status = "verified"
            newly_verified = True
            verified_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            "UPDATE incidents SET upvotes = ?, status = ?, verified_at = ? WHERE id = ?",
            (total_confirmations, status, verified_at, incident_id),
        )

    # Scoring integration: incrementally update scores if verified
    if newly_verified and G and HOUR_SCORES:
        with db_conn() as conn:
            update_incremental_scores(
                G, HOUR_SCORES,
                inc.get("edge_u"), inc.get("edge_v"), inc.get("edge_key", 0),
                conn,
            )
        logger.info("Incident #%d verified! Edge scores refreshed.", incident_id)

    return jsonify({
        "status": "ok",
        "incident_id": incident_id,
        "upvotes": total_confirmations,
        "confirmations": total_confirmations,
        "verified": (status == "verified"),
        "newly_verified": newly_verified,
        "message": "Incident verified! It is now live on the map and safety scores have updated." if newly_verified else "Confirmation recorded.",
    })


@app.route("/api/incidents/pending")
def api_incidents_pending():
    lat_q    = request.args.get("lat")
    lon_q    = request.args.get("lon")
    radius_q = request.args.get("radius")

    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM incidents
            WHERE status = 'pending'
            ORDER BY reported_at DESC
            """
        ).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        d["type"] = d.get("itype")
        d["street_name"] = _get_street_name_for_edge(d.get("edge_u"), d.get("edge_v"), d.get("edge_key", 0))
        d["confirmations"] = d.get("upvotes", 1)
        d["confirmations_needed"] = VERIFY_THRESHOLD

        dist = 0.0
        if lat_q and lon_q:
            try:
                dist = haversine_m(float(lat_q), float(lon_q), d["lat"], d["lon"])
                d["distance_m"] = round(dist, 1)
                if radius_q and dist > float(radius_q):
                    continue
            except Exception:
                pass
        items.append(d)

    return jsonify({"pending_incidents": items, "count": len(items)})


@app.route("/api/sos", methods=["POST"])
def api_sos():
    """
    POST /api/sos — Phase 2 stub.
    Returns 501 so the frontend can show a 'Coming soon' message.
    """
    return jsonify({"status": "not_implemented", "message": "SOS is coming in Phase 2."}), 501


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    startup()
    app.run(host="0.0.0.0", port=5000, debug=False)
