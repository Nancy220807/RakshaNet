import json
from app import app, startup
from config import DEMO_ROUTES

startup()
with app.test_client() as c:
    golden = {}
    for p in DEMO_ROUTES:
        name = p['name']
        golden[name] = {}
        for h in [18, 23]:
            r = c.get("/api/route", query_string={
                "start_lat": p["start"][0],
                "start_lon": p["start"][1],
                "end_lat": p["end"][0],
                "end_lon": p["end"][1],
                "hour": h,
            })
            d = r.get_json()
            golden[name][h] = {
                "short_dist": d["shortest"]["total_distance_m"],
                "short_score": d["shortest"]["avg_score"],
                "safe_dist": d["safest"]["total_distance_m"],
                "safe_score": d["safest"]["avg_score"],
            }
    sr = c.get("/api/search?q=hauz").get_json()
    items = sr.get("results") if isinstance(sr, dict) else sr
    golden["search_hauz"] = len(items)
    print("GOLDEN VALUES CAPTURED:")
    print(json.dumps(golden, indent=2))
    with open("data/golden_phase12.json", "w") as f:
        json.dump(golden, f, indent=2)
