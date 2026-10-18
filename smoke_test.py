"""
smoke_test.py — Run once to verify the full pipeline works.
Usage: python smoke_test.py
"""
import logging
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

print("=== Step 1: Graph (download first time / load from cache) ===")
from graph_loader import get_graph, get_bbox
G = get_graph()
north, south, east, west = get_bbox(G)
print(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")
print(f"BBox: N={north:.4f} S={south:.4f} E={east:.4f} W={west:.4f}")

print("\n=== Step 2: Seed DB ===")
from seed_data import run_seed
run_seed()

print("\n=== Step 3: Precompute scores ===")
from scoring import precompute_scores, score_to_band
hour_scores = precompute_scores(G)
print(f"hour_scores keys: {sorted(hour_scores.keys())[:5]} … (24 total)")
sample_edge = next(iter(hour_scores[18]))
s18 = hour_scores[18][sample_edge]
s23 = hour_scores[23][sample_edge]
b18, _ = score_to_band(s18)
b23, _ = score_to_band(s23)
print(f"Sample edge {sample_edge}: 18h={s18:.1f} ({b18}), 23h={s23:.1f} ({b23})")

print("\n=== Step 4: Route smoke test (6 PM vs 11 PM) ===")
from routing import snap_to_node, compute_routes
from config import SAMPLE_START, SAMPLE_END

origin = snap_to_node(G, SAMPLE_START[0], SAMPLE_START[1])
dest   = snap_to_node(G, SAMPLE_END[0],   SAMPLE_END[1])
print(f"Origin node: {origin}, Dest node: {dest}")

for hour in [18, 23]:
    short, safe, err = compute_routes(G, origin, dest, hour, hour_scores)
    if err:
        print(f"  Hour {hour}h ERROR: {err}")
        continue
    print(
        f"  {hour:02d}h | "
        f"Shortest: {short['total_distance_m']:.0f}m "
        f"avg={short['avg_score']:.1f} avoid={short['avoid_count']} | "
        f"Safest:   {safe['total_distance_m']:.0f}m "
        f"avg={safe['avg_score']:.1f} avoid={safe['avoid_count']}"
    )
    print(f"       Verdict: {safe['verdict']}")
    for i, seg in enumerate(safe["segments"][:4]):
        band, _ = score_to_band(seg["score"])
        print(f"       seg[{i}] score={seg['score']:.1f} band={band} hw={seg['highway']}")

# Check that at least one segment changes band between 6 PM and 11 PM
print("\n=== Step 5: Band-shift verification ===")
band_shifts = 0
for edge_key in hour_scores[18]:
    s18 = hour_scores[18][edge_key]
    s23 = hour_scores[23][edge_key]
    b18, _ = score_to_band(s18)
    b23, _ = score_to_band(s23)
    if b18 != b23:
        band_shifts += 1

print(f"Edges that change safety band from 18h to 23h: {band_shifts}")
assert band_shifts > 0, "No band shifts detected — demo won't work!"

print("\n✅  ALL CHECKS PASSED — run `python app.py` to start the server.")
