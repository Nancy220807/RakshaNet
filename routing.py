"""
routing.py — Shortest and safest route computation for RakshaNet.

Public API:
    snap_to_node(G, lat, lon)                        -> node_id
    compute_routes(G, origin, dest, hour, scores)    -> (shortest_info, safest_info)
    route_to_geojson(G, path, scores)                -> route_dict

All tunables (K, DETOUR_CAP, K_STEP) live in config.py.
"""

import math
import logging
from typing import Optional

import networkx as nx
import osmnx as ox

from config import (
    ROUTING_K, DETOUR_CAP, K_STEP,
    CENTER_LAT, CENTER_LON, RADIUS_M,
    WALK_SPEED_KMH,
)
from scoring import score_to_band

logger = logging.getLogger(__name__)


# ── Node snapping ─────────────────────────────────────────────────────────────

def snap_to_node(G, lat: float, lon: float) -> int:
    """
    Return the nearest graph node to (lat, lon).
    Raises ValueError if the point is outside the area boundary (rough check).
    """
    # Rough distance check to centre (degrees, not metres)
    dlat = abs(lat - CENTER_LAT)
    dlon = abs(lon - CENTER_LON)
    # 1 deg ≈ 111 km; RADIUS_M + 20% buffer
    limit_deg = (RADIUS_M * 1.2) / 111_000
    if dlat > limit_deg or dlon > limit_deg:
        raise ValueError(
            f"Point ({lat:.4f}, {lon:.4f}) is outside the RakshaNet coverage area."
        )

    node = ox.nearest_nodes(G, lon, lat)
    return int(node)


# ── Route segment builder ─────────────────────────────────────────────────────

def _build_route_dict(G, path: list, scores: dict, route_type: str) -> dict:
    """
    Given an ordered list of node IDs, build a dict with:
      - segments: [{coords, score, band, color, length_m}]
      - total_distance_m, eta_min, avg_score, min_score, avoid_count
    """
    segments    = []
    total_dist  = 0.0
    score_sum   = 0.0
    min_score   = 100.0
    avoid_count = 0

    for i in range(len(path) - 1):
        u = path[i]
        v = path[i + 1]

        # MultiDiGraph may have multiple edges; pick the one with minimum length
        edge_data_dict = G.get_edge_data(u, v)
        if not edge_data_dict:
            continue
        # edge_data_dict is {key: data_dict, ...}
        key, data = min(edge_data_dict.items(), key=lambda kv: kv[1].get("length", 0))

        length_m = data.get("length", 0.0)
        score    = scores.get((u, v, key), scores.get((v, u, key), 50.0))
        band, color = score_to_band(score)

        # Geometry
        if "geometry" in data:
            coords = [[pt[1], pt[0]] for pt in data["geometry"].coords]
        else:
            coords = [
                [G.nodes[u]["y"], G.nodes[u]["x"]],
                [G.nodes[v]["y"], G.nodes[v]["x"]],
            ]

        highway = data.get("highway", "unclassified")
        if isinstance(highway, list):
            highway = highway[0]

        segments.append({
            "coords":   coords,
            "score":    score,
            "band":     band,
            "color":    color,
            "length_m": round(length_m, 1),
            "highway":  highway,
        })

        total_dist  += length_m
        score_sum   += score
        min_score    = min(min_score, score)
        if band == "avoid":
            avoid_count += 1

    n_seg = len(segments)
    avg_score = round(score_sum / n_seg, 1) if n_seg else 0.0
    eta_min   = round((total_dist / 1000.0) / WALK_SPEED_KMH * 60, 1)

    return {
        "type":           route_type,
        "segments":       segments,
        "total_distance_m": round(total_dist, 1),
        "eta_min":        eta_min,
        "avg_score":      avg_score,
        "min_score":      round(min_score, 1) if n_seg else 0.0,
        "avoid_count":    avoid_count,
        "node_path":      path,
    }


# ── Edge weight builders ──────────────────────────────────────────────────────

def _add_length_weights(G):
    """Set 'weight' attribute on all edges to their length in metres."""
    for u, v, key, data in G.edges(keys=True, data=True):
        G[u][v][key]["_w_length"] = data.get("length", 1.0)


def _add_safety_weights(G, scores: dict, k: float):
    """
    Set safety-aware weight on all edges:
        cost = length * (1 + K * (100 - score) / 100)
    Higher score → lower cost → preferred by shortest_path.
    """
    for u, v, key, data in G.edges(keys=True, data=True):
        length = data.get("length", 1.0)
        score  = scores.get((u, v, key), scores.get((v, u, key), 50.0))
        cost   = length * (1.0 + k * (100.0 - score) / 100.0)
        G[u][v][key]["_w_safety"] = cost


# ── Main compute function ─────────────────────────────────────────────────────

def compute_routes(
    G,
    origin_node: int,
    dest_node: int,
    hour: int,
    hour_scores: dict,
) -> tuple[Optional[dict], Optional[dict], Optional[str]]:
    """
    Compute shortest and safest routes between origin and destination.

    Returns:
        (shortest_dict, safest_dict, error_message)
        On success: error_message is None.
        On failure: dicts are None and error_message describes the problem.
    """
    scores = hour_scores.get(hour, {})

    # ── Shortest route (by distance) ─────────────────────────────────────────
    try:
        shortest_path = nx.shortest_path(G, origin_node, dest_node, weight="length")
    except nx.NetworkXNoPath:
        return None, None, "No path found between the selected points."
    except nx.NodeNotFound as e:
        return None, None, f"Node not found: {e}"

    shortest_info = _build_route_dict(G, shortest_path, scores, "shortest")
    shortest_dist = shortest_info["total_distance_m"]

    if shortest_dist <= 0:
        return None, None, "Route has zero length — start and end may be the same point."

    # ── Safest route (with detour cap) ────────────────────────────────────────
    k = ROUTING_K
    safest_path = None

    while k > 0:
        # Build temporary weight attribute for this K
        _add_safety_weights(G, scores, k)
        try:
            candidate = nx.shortest_path(G, origin_node, dest_node, weight="_w_safety")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            break

        candidate_info = _build_route_dict(G, candidate, scores, "safest")
        candidate_dist = candidate_info["total_distance_m"]

        # Accept if within detour cap
        if candidate_dist <= shortest_dist * DETOUR_CAP:
            safest_path  = candidate
            safest_info  = candidate_info
            break

        k -= K_STEP
        logger.debug(
            "Detour cap exceeded (%.0f m > %.0f m × %.1f); reducing K to %.1f",
            candidate_dist, shortest_dist, DETOUR_CAP, k,
        )

    if safest_path is None:
        # Fall back to shortest route if even K=0 exceeds cap (shouldn't happen)
        safest_path = shortest_path
        safest_info = _build_route_dict(G, safest_path, scores, "safest")

    # ── Verdict ───────────────────────────────────────────────────────────────
    extra_m   = safest_info["total_distance_m"] - shortest_info["total_distance_m"]
    avoids_saved = shortest_info["avoid_count"] - safest_info["avoid_count"]

    if extra_m <= 20 and safest_info["avg_score"] <= shortest_info["avg_score"] + 1:
        verdict = "Both routes are essentially the same for this time of day."
    elif safest_info["avg_score"] > shortest_info["avg_score"]:
        parts = []
        if extra_m > 5:
            parts.append(f"{int(extra_m)} m longer")
        if avoids_saved > 0:
            parts.append(f"avoids {avoids_saved} high-risk segment{'s' if avoids_saved > 1 else ''}")
        if safest_info["avg_score"] - shortest_info["avg_score"] >= 3:
            parts.append(
                f"avg safety +{safest_info['avg_score'] - shortest_info['avg_score']:.0f} pts"
            )
        verdict = "Safest route is " + (", ".join(parts) if parts else "slightly safer") + "."
    else:
        verdict = "Shortest route is already the safest option here."

    shortest_info["verdict"] = verdict
    safest_info["verdict"]   = verdict

    return shortest_info, safest_info, None
