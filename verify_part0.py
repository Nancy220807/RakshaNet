import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import networkx as nx
from graph_loader import get_graph
from scoring import precompute_scores
from routing import compute_routes, snap_to_node
from config import DEMO_ROUTES, BAND_SAFE, BAND_CAUTION

def verify():
    print("=" * 65)
    print("PART 0 VERIFICATION: DEMO ROUTE PRESETS & DIVERGENCE")
    print("=" * 65)

    G = get_graph()
    hour_scores = precompute_scores(G)

    all_passed = True

    for preset in DEMO_ROUTES:
        name = preset["name"]
        s_coords = preset["start"]
        e_coords = preset["end"]

        u = snap_to_node(G, s_coords[0], s_coords[1])
        v = snap_to_node(G, e_coords[0], e_coords[1])

        print(f"\n[Preset] {name}")
        
        # 18:00 Check
        s_18, safe_18, _ = compute_routes(G, u, v, 18, hour_scores)
        bands_18 = [seg["band"] for seg in safe_18["segments"]]
        green_18 = bands_18.count("safe")
        
        # 22:00 and 23:00 Check
        s_22, safe_22, _ = compute_routes(G, u, v, 22, hour_scores)
        s_nodes_22 = set(s_22["node_path"])
        safe_nodes_22 = set(safe_22["node_path"])
        overlap_22 = len(s_nodes_22.intersection(safe_nodes_22)) / max(len(s_nodes_22), 1) * 100

        s_23, safe_23, _ = compute_routes(G, u, v, 23, hour_scores)
        s_nodes_23 = set(s_23["node_path"])
        safe_nodes_23 = set(safe_23["node_path"])
        overlap_23 = len(s_nodes_23.intersection(safe_nodes_23)) / max(len(s_nodes_23), 1) * 100
        bands_23 = [seg["band"] for seg in safe_23["segments"]]
        green_23 = bands_23.count("safe")
        band_changes = sum(1 for b1, b2 in zip(bands_18[:min(len(bands_18), len(bands_23))], bands_23) if b1 != b2)

        print(f"  @ 18:00: Shortest={s_18['total_distance_m']:.1f}m, Safest={safe_18['total_distance_m']:.1f}m | Green segments={green_18}/{len(bands_18)}")
        print(f"  @ 22:00: Shortest={s_22['total_distance_m']:.1f}m, Safest={safe_22['total_distance_m']:.1f}m | Overlap={overlap_22:.1f}%")
        print(f"  @ 23:00: Shortest={s_23['total_distance_m']:.1f}m, Safest={safe_23['total_distance_m']:.1f}m | Overlap={overlap_23:.1f}% | Green segments={green_23}/{len(bands_23)} | Band changes={band_changes}")

        if overlap_22 > 70:
            print(f"  ❌ FAIL: Overlap at 22:00 is {overlap_22:.1f}% (> 70%)")
            all_passed = False
        else:
            print(f"  ✅ PASS: Clean divergence at 22:00 ({overlap_22:.1f}% <= 70%)")

        if green_18 == 0:
            print("  ❌ FAIL: No green segments at 18:00")
            all_passed = False
        else:
            print(f"  ✅ PASS: Visible green segments at 18:00 ({green_18} segments)")

        if band_changes == 0:
            print("  ❌ FAIL: No segments changed band between 18:00 and 23:00")
            all_passed = False
        else:
            print(f"  ✅ PASS: Segments changed band by 23:00 ({band_changes} segments changed)")

    print("\n" + "=" * 65)
    print("OVERALL PART 0 RESULT:", "ALL PASSED ✅" if all_passed else "FAIL ❌")
    print("=" * 65)

if __name__ == "__main__":
    verify()
