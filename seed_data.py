"""
seed_data.py — Deterministic, idempotent seeding of edge attributes and
               synthetic incidents for the RakshaNet demo.

Run once before starting the app:
    python seed_data.py

Re-running is safe: edge_attrs and incidents tables are wiped and re-seeded
so the data is always consistent with config.py.

random.seed(42) ensures identical output on every machine.
"""

import random
import logging
import os
from datetime import datetime, timedelta

import osmnx as ox

from config import (
    LIGHTING_BY_TYPE, LIGHTING_DEFAULT, LIGHTING_NOISE,
    CROWD_BY_TYPE, CROWD_DEFAULT,
    INCIDENT_SEED, NUM_INCIDENTS, INCIDENT_RADIUS_M,
    CENTER_LAT, CENTER_LON,
    DATA_DIR,
)
from db import init_db, db_conn, upsert_edge_attrs, insert_incident
from graph_loader import get_graph

logger = logging.getLogger(__name__)

# ── Incident clusters ─────────────────────────────────────────────────────────
# Each cluster: (lat, lon, radius_m, weight)
# Clustered on known less-lit inner lanes around Hauz Khas / Green Park area.
INCIDENT_CLUSTERS = [
    (28.5435, 77.2068, 120, 0.30),   # Hauz Khas Village inner lanes
    (28.5480, 77.2010, 100, 0.25),   # Deer Park back path
    (28.5520, 77.2055, 90,  0.20),   # Safdarjung Enclave service roads
    (28.5550, 77.2080, 80,  0.15),   # Green Park Market back lane
    (28.5460, 77.1980, 70,  0.10),   # IIT Delhi gate area path
]

INCIDENT_TYPES = ["harassment", "poor_lighting", "unsafe_area", "other"]
INCIDENT_TYPE_WEIGHTS = [0.35, 0.30, 0.25, 0.10]


def _pick_cluster_point(rng):
    """Pick a lat/lon from the weighted cluster distribution."""
    weights = [c[3] for c in INCIDENT_CLUSTERS]
    cluster = rng.choices(INCIDENT_CLUSTERS, weights=weights, k=1)[0]
    clat, clon, radius_m, _ = cluster
    # Offset in degrees (approx: 1 deg lat ≈ 111 km, 1 deg lon ≈ 88 km at 28°N)
    dlat = rng.uniform(-radius_m / 111_000, radius_m / 111_000)
    dlon = rng.uniform(-radius_m / 88_000,  radius_m / 88_000)
    return clat + dlat, clon + dlon


def seed_edge_attrs(G, conn):
    """
    Compute and store lighting + crowd base values for every graph edge.
    Uses OSM `lit` tag when present; otherwise seeds by highway type + noise.
    """
    rng = random.Random(INCIDENT_SEED)
    rows = []

    for u, v, key, data in G.edges(keys=True, data=True):
        highway = data.get("highway", "unclassified")
        # OSMnx sometimes stores highway as a list (e.g. ['residential', 'service'])
        if isinstance(highway, list):
            highway = highway[0]

        # ── Lighting ──────────────────────────────────────────────────────────
        lit_tag = data.get("lit", None)
        if lit_tag == "yes":
            lighting = 85.0
        elif lit_tag == "no":
            lighting = 20.0
        else:
            base = LIGHTING_BY_TYPE.get(highway, LIGHTING_DEFAULT)
            noise = rng.uniform(-LIGHTING_NOISE, LIGHTING_NOISE)
            lighting = max(0.0, min(100.0, base + noise))

        # ── Crowd base ────────────────────────────────────────────────────────
        crowd_base_val = CROWD_BY_TYPE.get(highway, CROWD_DEFAULT)
        crowd_noise    = rng.uniform(-5, 5)
        crowd_base     = max(0.0, min(100.0, crowd_base_val + crowd_noise))

        rows.append({
            "u":          u,
            "v":          v,
            "key":        key,
            "lighting":   round(lighting, 2),
            "crowd_base": round(crowd_base, 2),
            "highway":    highway,
        })

    # Wipe and re-seed for idempotency
    conn.execute("DELETE FROM edge_attrs")
    upsert_edge_attrs(conn, rows)
    logger.info("Seeded %d edge attribute rows.", len(rows))


def seed_incidents(G, conn):
    """
    Seed NUM_INCIDENTS verified incidents clustered on known unsafe spots.
    Snaps each incident lat/lon to the nearest graph edge (within INCIDENT_RADIUS_M).
    """
    rng = random.Random(INCIDENT_SEED + 1)   # different stream from edge seeding

    conn.execute("DELETE FROM incidents")

    now = datetime.now()
    seeded = 0

    for _ in range(NUM_INCIDENTS * 3):   # oversample to hit NUM_INCIDENTS snapped
        lat, lon = _pick_cluster_point(rng)
        itype    = rng.choices(INCIDENT_TYPES, weights=INCIDENT_TYPE_WEIGHTS, k=1)[0]
        upvotes  = rng.randint(2, 12)   # always ≥ 2 so they count as verified
        days_ago = rng.uniform(0, 28)
        ts       = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")

        # Snap to nearest graph edge (osmnx 2.x: top-level ox.nearest_edges)
        try:
            ne = ox.nearest_edges(G, lon, lat)
            # ne is (u, v, key)
            edge_u, edge_v, edge_key = int(ne[0]), int(ne[1]), int(ne[2])
        except Exception:
            continue

        insert_incident(conn, lat, lon, edge_u, edge_v, edge_key,
                         itype, "verified", upvotes, ts)
        seeded += 1
        if seeded >= NUM_INCIDENTS:
            break

    logger.info("Seeded %d verified incidents.", seeded)


def run_seed():
    """Entry point: initialise DB, load graph, seed everything."""
    os.makedirs(DATA_DIR, exist_ok=True)
    init_db()
    G = get_graph()

    with db_conn() as conn:
        seed_edge_attrs(G, conn)
        seed_incidents(G, conn)

    logger.info("Seeding complete.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    run_seed()
    print("\n✅  Seed complete. Run `python app.py` to start the server.")
