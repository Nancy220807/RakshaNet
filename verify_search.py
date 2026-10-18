"""verify_search.py — API verification script for /api/search and /api/nearest."""
import urllib.request, json

BASE = "http://localhost:5000"

def get(path):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())

tests = [
    ("hauz",            "/api/search?q=hauz"),
    ("green park",      "/api/search?q=green%20park"),
    ("gali",            "/api/search?q=gali"),
    ("latlon paste",    "/api/search?q=28.5494%2C%2077.2001"),
    ("nonsense",        "/api/search?q=xyzfoo123abc"),
    ("empty q",         "/api/search?q="),
    ("nearest",         "/api/nearest?lat=28.5433&lon=77.2065"),
]

all_pass = True
north, south, east, west = 28.5673, 28.5315, 77.2206, 77.1796  # from /api/meta

print("=" * 60)
for label, path in tests:
    status, data = get(path)
    assert status == 200, f"{label}: HTTP {status}"

    if "results" in data:
        results = data["results"]
        print(f"[{label}] {len(results)} result(s):")
        for r in results[:4]:
            in_bbox = (south <= r["lat"] <= north) and (west <= r["lon"] <= east)
            bbox_ok = "IN-BBOX" if in_bbox else "OUT-OF-BBOX!"
            if not in_bbox and r["source"] != "place":
                all_pass = False
            print(f"    {r['source']:8s} | {bbox_ok:12s} | {r['label']}")
    else:
        print(f"[{label}] {data}")

print("=" * 60)
print("ALL HTTP 200 (no 500s) -", "PASS" if all_pass else "FAIL (bbox issue)")
