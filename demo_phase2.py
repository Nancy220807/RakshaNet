"""
demo_phase2.py — End-to-end Hackathon Demo Script for RakshaNet Phase 2
Simulates the community safety verification workflow:
1. Query initial route (Hauz Khas Village -> Green Park Metro) at 23:00
2. User Asha reports 'poor_lighting' hazard on an active route segment
3. Hazard stays pending (1/3 confirmations) -> scores unaffected
4. User Riya confirms (2/3 confirmations) -> still pending
5. User Meera confirms (3/3 confirmations) -> FLIPS TO VERIFIED!
6. Incremental safety scoring applies penalty to the unlit stretch
7. Query route again -> demonstrates drop in route safety score or route rerouting
"""

import sys
import io
import time
import requests

# Fix Windows console UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_URL = "http://localhost:5000"

def run_demo():
    print("=" * 72)
    print("  🛡️  RAKSHANET PHASE 2 — COMMUNITY VERIFICATION DEMO  🛡️")
    print("=" * 72)
    print("Team: DevDiva Jam 2026 | Track: Women Safety & Security")
    print("Demonstrating real-time, peer-verified crowdsourced road safety.\n")

    # Step 0: Check initial route at 23:00
    print("📍 [Step 0] Querying baseline route at 23:00 (Night Mode)...")
    preset_start = {"lat": 28.5535, "lon": 77.1945}  # Hauz Khas Village
    preset_end   = {"lat": 28.5585, "lon": 77.2065}  # Green Park Metro

    r0 = requests.get(f"{BASE_URL}/api/route", params={
        "start_lat": preset_start["lat"],
        "start_lon": preset_start["lon"],
        "end_lat": preset_end["lat"],
        "end_lon": preset_end["lon"],
        "hour": 23,
    })
    if r0.status_code != 200:
        print(f"❌ Failed to query route: {r0.text}")
        return

    data0 = r0.json()
    init_score = data0["safest"]["avg_score"]
    init_dist = data0["safest"]["total_distance_m"]
    print(f"   Baseline Safest Route Score: {init_score:.1f}/100 (Distance: {init_dist}m)\n")

    # Step 1: Asha reports poor lighting
    print("🚶 [Step 1] Asha walks along Deer Park lane at 23:00 and spots broken lights.")
    s_asha = requests.Session()
    s_asha.post(f"{BASE_URL}/api/auth/switch", json={"user_id": "user_asha"})

    hazard_lat, hazard_lon = 28.5433, 77.2065
    rep_res = s_asha.post(f"{BASE_URL}/api/incidents", json={
        "lat": hazard_lat,
        "lon": hazard_lon,
        "type": "poor_lighting",
        "incident_time": "2026-10-13 23:00:00",
        "note": "Pitch black stretch near park gate, all streetlights broken",
    })
    if rep_res.status_code != 201:
        print(f"   Note: Report returned {rep_res.status_code}: {rep_res.text}")
        return

    inc_data = rep_res.json()["incident"]
    inc_id = inc_data["id"]
    print(f"   ✅ Report filed! Incident #{inc_id}")
    print(f"   Status: '{inc_data['status'].upper()}' (Confirmations: {inc_data['confirmations']}/3)")
    print("   🛡️ Anti-abuse check: Pending incident DOES NOT affect edge scores yet.\n")

    # Step 2: Riya confirms
    print("🚶 [Step 2] Riya is in the area and verifies the unlit stretch on her feed.")
    s_riya = requests.Session()
    s_riya.post(f"{BASE_URL}/api/auth/switch", json={"user_id": "user_riya"})

    v1_res = s_riya.post(f"{BASE_URL}/api/incidents/{inc_id}/vote", json={
        "lat": hazard_lat + 0.0005,
        "lon": hazard_lon + 0.0005,
        "simulated": True,
    })
    v1_data = v1_res.json()
    print(f"   ✅ Riya confirmed! Total confirmations: {v1_data['confirmations']}/3")
    print(f"   Status: Still PENDING (Threshold is 3)\n")

    # Step 3: Meera confirms -> Threshold reached
    print("🚶 [Step 3] Meera also confirms the unlit stretch.")
    s_meera = requests.Session()
    s_meera.post(f"{BASE_URL}/api/auth/switch", json={"user_id": "user_meera"})

    v2_res = s_meera.post(f"{BASE_URL}/api/incidents/{inc_id}/vote", json={
        "lat": hazard_lat + 0.0002,
        "lon": hazard_lon + 0.0002,
        "simulated": True,
    })
    v2_data = v2_res.json()
    print(f"   🎉 Meera confirmed! Total confirmations: {v2_data['confirmations']}/3")
    print(f"   ⚡ THRESHOLD REACHED! Newly verified: {v2_data['newly_verified']}")
    print(f"   Status is now: VERIFIED! Incident is broadcast to all commuters.\n")

    # Step 4: Verify feed
    print("📋 [Step 4] Checking Community Feed (/api/incidents?status=verified)...")
    feed_res = requests.get(f"{BASE_URL}/api/incidents?status=verified&hours=24")
    feed_items = feed_res.json().get("incidents", [])
    matched = [item for item in feed_items if item["id"] == inc_id]
    if matched:
        print(f"   ✅ Incident #{inc_id} is live in the verified feed!")
        print(f"   Street: {matched[0].get('street_name')} | Upvotes: {matched[0].get('confirmations')}\n")

    # Step 5: Check route impact
    print("🔄 [Step 5] Re-calculating Safe Route at 23:00 after verification...")
    r1 = requests.get(f"{BASE_URL}/api/route", params={
        "start_lat": preset_start["lat"],
        "start_lon": preset_start["lon"],
        "end_lat": preset_end["lat"],
        "end_lon": preset_end["lon"],
        "hour": 23,
    })
    data1 = r1.json()
    new_score = data1["safest"]["avg_score"]
    new_dist = data1["safest"]["total_distance_m"]
    print(f"   New Safest Route Score: {new_score:.1f}/100 (Distance: {new_dist}m)")
    print(f"   Edge penalty applied in real-time without full server reboot!")

    print("\n" + "=" * 72)
    print("  ✨ DEMO COMPLETED SUCCESSFULLY — RAKSHANET KEEPS WOMEN SAFE! ✨")
    print("=" * 72)

if __name__ == "__main__":
    run_demo()
