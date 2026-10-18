"""
graph_loader.py — Download (once) and cache the OSM walk graph for the area.

Usage:
    from graph_loader import get_graph
    G = get_graph()   # downloads if needed, loads from cache otherwise
"""

import os
import logging

import osmnx as ox

from config import CENTER_LAT, CENTER_LON, RADIUS_M, GRAPH_PATH, DATA_DIR

logger = logging.getLogger(__name__)


def get_graph():
    """
    Return the OSMnx walk graph for the configured area.

    - First call: downloads from OpenStreetMap and saves to GRAPH_PATH (~30-60 s).
    - Subsequent calls: loads from the cached .graphml file (< 2 s).
    - The graph is projected to UTM so edge lengths (in metres) are accurate.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(GRAPH_PATH):
        logger.info("Loading graph from cache: %s", GRAPH_PATH)
        G = ox.load_graphml(GRAPH_PATH)
        logger.info("Graph loaded — %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
        return G

    logger.info(
        "Downloading OSM walk graph for (%.4f, %.4f) r=%dm …",
        CENTER_LAT, CENTER_LON, RADIUS_M,
    )
    # Download walk network; osmnx 2.x automatically adds 'length' (metres) to edges
    G = ox.graph_from_point(
        (CENTER_LAT, CENTER_LON),
        dist=RADIUS_M,
        network_type="walk",
        retain_all=False,
    )

    logger.info("Saving graph to %s", GRAPH_PATH)
    ox.save_graphml(G, filepath=GRAPH_PATH)
    logger.info(
        "Graph saved — %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges()
    )
    return G


def get_bbox(G):
    """Return (north, south, east, west) bounding box of the graph."""
    lats = [data["y"] for _, data in G.nodes(data=True)]
    lons = [data["x"] for _, data in G.nodes(data=True)]
    return max(lats), min(lats), max(lons), min(lons)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    G = get_graph()
    north, south, east, west = get_bbox(G)
    print(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")
    print(f"BBox: N={north:.4f} S={south:.4f} E={east:.4f} W={west:.4f}")
