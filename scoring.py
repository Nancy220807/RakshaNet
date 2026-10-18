"""
scoring.py — Safety score engine for RakshaNet.

Public API:
    precompute_scores(G)  -> hour_scores: dict[hour -> dict[(u,v,key) -> score]]
    score_edge(u, v, key, hour, edge_attrs, incident_index) -> float (0-100)
    score_to_band(score) -> ("safe"|"caution"|"avoid", hex_color)

All weights and thresholds live in config.py.
"""

import logging
from collections import defaultdict

from config import (
    W_LIGHTING, W_CROWD, W_INCIDENT, W_TIME,
    CROWD_HOURLY_MULTIPLIER, TIME_FACTOR_BY_HOUR,
    INCIDENT_PENALTY,
    BAND_SAFE, BAND_CAUTION,
)
from db import db_conn, load_edge_attrs, load_verified_incidents

logger = logging.getLogger(__name__)

# Colour palette aligned with brand (#E91E8C accent)
COLOR_SAFE    = "#4CAF50"   # green
COLOR_CAUTION = "#FF9800"   # orange
COLOR_AVOID   = "#F44336"   # red


# ── Band helper ───────────────────────────────────────────────────────────────

def score_to_band(score: float):
    """
    Map a 0-100 score to a (band_name, hex_color) tuple.
    Thresholds defined in config.BAND_SAFE / BAND_CAUTION.
    """
    if score >= BAND_SAFE:
        return "safe", COLOR_SAFE
    elif score >= BAND_CAUTION:
        return "caution", COLOR_CAUTION
    else:
        return "avoid", COLOR_AVOID


from datetime import datetime

def _parse_ts(ts_str):
    if not ts_str:
        return datetime.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(ts_str).split("+")[0], fmt)
        except ValueError:
            pass
    return datetime.now()


# ── Per-edge scorer ───────────────────────────────────────────────────────────

def score_edge(
    u: int,
    v: int,
    key: int,
    hour: int,
    edge_attrs: dict,          # (u,v,key) -> {lighting, crowd_base, ...}
    incident_index: dict,      # (u,v,key) -> list of verified incident dicts
    now: datetime = None,
) -> float:
    """
    Compute safety score (0-100) for a single edge at a given hour.

    score = W_LIGHTING*lighting + W_CROWD*crowd + W_INCIDENT*incident + W_TIME*time_factor
    Includes:
      - Time-of-day awareness (+-3h around incident time)
      - Age decay over 30 days
      - poor_lighting reducing lighting component
    """
    if now is None:
        now = datetime.now()

    attr = edge_attrs.get((u, v, key)) or edge_attrs.get((v, u, key))

    if attr is None:
        lighting   = 50.0
        crowd_base = 35.0
    else:
        lighting   = float(attr["lighting"])
        crowd_base = float(attr["crowd_base"])

    # ── Crowd: apply hourly multiplier ────────────────────────────────────────
    multiplier = CROWD_HOURLY_MULTIPLIER[hour % 24]
    crowd      = crowd_base * multiplier

    # ── Incident: time-aware penalty + age decay ──────────────────────────────
    incidents = incident_index.get((u, v, key), []) + incident_index.get((v, u, key), [])
    total_penalty = 0.0
    lighting_reduction = 0.0

    for inc in incidents:
        inc_ts_str = inc.get("incident_time") or inc.get("reported_at")
        inc_dt     = _parse_ts(inc_ts_str)
        days_ago   = max(0.0, (now - inc_dt).total_seconds() / 86400.0)
        age_decay  = max(0.0, min(1.0, (30.0 - days_ago) / 30.0))

        # Full penalty within +-3h of incident hour; half penalty otherwise
        inc_hour   = inc_dt.hour
        h_diff     = abs((hour % 24) - inc_hour)
        h_diff     = min(h_diff, 24 - h_diff)
        time_mult  = 1.0 if h_diff <= 3 else 0.5

        total_penalty += INCIDENT_PENALTY * age_decay * time_mult

        if inc.get("itype") == "poor_lighting":
            lighting_reduction += 25.0 * age_decay

    incident_comp = max(0.0, 100.0 - min(80.0, total_penalty))
    lighting      = max(0.0, lighting - lighting_reduction)

    # ── Time-of-day factor ────────────────────────────────────────────────────
    time_factor = float(TIME_FACTOR_BY_HOUR[hour % 24])

    # ── Weighted sum ──────────────────────────────────────────────────────────
    raw = (
        W_LIGHTING  * lighting      +
        W_CROWD     * crowd         +
        W_INCIDENT  * incident_comp +
        W_TIME      * time_factor
    )
    return round(max(0.0, min(100.0, raw)), 2)


# ── Incident index builder ────────────────────────────────────────────────────

def build_incident_index(conn) -> dict:
    """
    Return a dict: (edge_u, edge_v, edge_key) -> list of verified incident dicts.
    Only incidents from the last 30 days are counted.
    """
    incidents = load_verified_incidents(conn, days=30)
    index: dict = defaultdict(list)
    for inc in incidents:
        if inc.get("edge_u") is not None and inc.get("edge_v") is not None:
            key = (inc["edge_u"], inc["edge_v"], inc.get("edge_key", 0))
            index[key].append(inc)
    return dict(index)


def update_incremental_scores(G, hour_scores: dict, edge_u: int, edge_v: int, edge_key: int, conn):
    """
    Incrementally recompute scores for the affected edge and its immediate neighbors.
    Updates hour_scores in-place across all 24 hours.
    """
    edge_attrs     = load_edge_attrs(conn)
    incident_index = build_incident_index(conn)

    affected_edges = set()
    if edge_u is not None and edge_v is not None:
        affected_edges.add((edge_u, edge_v, edge_key))
        affected_edges.add((edge_v, edge_u, edge_key))
        for node in (edge_u, edge_v):
            if node in G:
                for nbr in G.successors(node):
                    for k in G[node][nbr]:
                        affected_edges.add((node, nbr, k))
                for pred in G.predecessors(node):
                    for k in G[pred][node]:
                        affected_edges.add((pred, node, k))

    now = datetime.now()
    for hour in range(24):
        scores = hour_scores[hour]
        for u, v, k in affected_edges:
            if G.has_edge(u, v, k):
                scores[(u, v, k)] = score_edge(u, v, k, hour, edge_attrs, incident_index, now=now)


# ── Precompute all hours ──────────────────────────────────────────────────────

def precompute_scores(G) -> dict:
    """
    Precompute safety scores for all edges × all 24 hours.

    Returns:
        hour_scores: dict[int hour -> dict[(u, v, key) -> float score]]

    Called once at app startup; results held in memory for fast API responses.
    """
    logger.info("Precomputing scores for all 24 hours …")

    with db_conn() as conn:
        edge_attrs      = load_edge_attrs(conn)
        incident_index  = build_incident_index(conn)

    edges = list(G.edges(keys=True))
    hour_scores: dict = {}

    for hour in range(24):
        scores = {}
        for u, v, key in edges:
            scores[(u, v, key)] = score_edge(u, v, key, hour, edge_attrs, incident_index)
        hour_scores[hour] = scores

    total_edges = len(edges)
    logger.info(
        "Precomputed %d hours × %d edges = %d score entries.",
        24, total_edges, 24 * total_edges,
    )

    # ── Demo calibration check ────────────────────────────────────────────────
    # Log a few incident-heavy edges to confirm 6 PM vs 11 PM differences
    if incident_index:
        sample_edge = next(iter(incident_index))
        s18 = hour_scores[18].get(sample_edge, -1)
        s23 = hour_scores[23].get(sample_edge, -1)
        logger.info(
            "Demo check — edge %s: score@18h=%.1f (%s), score@23h=%.1f (%s)",
            sample_edge,
            s18, score_to_band(s18)[0],
            s23, score_to_band(s23)[0],
        )

    return hour_scores


# ── Segment list for /api/segments ───────────────────────────────────────────

def build_segment_list(G, hour_scores: dict, hour: int) -> list:
    """
    Build the list of all segments for the heatmap overlay at a given hour.
    Returns a list of dicts: {coords, score, band, color, highway}.
    """
    scores = hour_scores.get(hour, {})
    segments = []

    for u, v, key, data in G.edges(keys=True, data=True):
        score = scores.get((u, v, key), 50.0)
        band, color = score_to_band(score)

        # Build coordinate list [[lat, lon], ...]
        if "geometry" in data:
            coords = [[pt[1], pt[0]] for pt in data["geometry"].coords]
        else:
            # Straight line between nodes
            n_data = G.nodes
            coords = [
                [n_data[u]["y"], n_data[u]["x"]],
                [n_data[v]["y"], n_data[v]["x"]],
            ]

        highway = data.get("highway", "unclassified")
        if isinstance(highway, list):
            highway = highway[0]

        segments.append({
            "u":      u,
            "v":      v,
            "key":    key,
            "coords": coords,
            "score":  score,
            "band":   band,
            "color":  color,
            "highway": highway,
        })

    return segments
