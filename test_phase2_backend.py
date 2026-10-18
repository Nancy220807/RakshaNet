"""test_phase2_backend.py — Verify Auth, Reporting, Voting, and Scoring integration."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import urllib.request, json
from http.cookiejar import CookieJar

BASE = "http://localhost:5000"

class Client:
    def __init__(self):
        self.cj = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def req(self, method, path, data=None):
        url = BASE + path
        body = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_data = json.loads(e.read().decode("utf-8"))
            except Exception:
                err_data = {"error": str(e)}
            return e.code, err_data

def run_tests():
    print("=" * 65)
    print("TESTING PHASE 2 BACKEND APIS")
    print("=" * 65)

    c_asha = Client()
    c_riya = Client()
    c_meera = Client()

    # 1. Test /api/auth/me (default asha in DEMO_MODE)
    st, res = c_asha.req("GET", "/api/auth/me")
    print(f"[/api/auth/me default] Status {st}, Authenticated={res.get('authenticated')}, User={res.get('user', {}).get('name')}")
    assert st == 200 and res.get("authenticated") is True and res.get("user", {}).get("name") == "Asha"

    # 2. Test /api/auth/switch to Riya
    st, res = c_riya.req("POST", "/api/auth/switch", {"user_id": "user_riya"})
    print(f"[/api/auth/switch -> Riya] Status {st}, User={res.get('user', {}).get('name')}")
    assert st == 200 and res.get("user", {}).get("name") == "Riya"

    # 3. Test /api/auth/switch to Meera
    st, res = c_meera.req("POST", "/api/auth/switch", {"user_id": "user_meera"})
    print(f"[/api/auth/switch -> Meera] Status {st}, User={res.get('user', {}).get('name')}")
    assert st == 200 and res.get("user", {}).get("name") == "Meera"

    # 4. Report an incident as Asha at a specific spot on the map
    # Hauz Khas inner lane: 28.5525, 77.2030
    inc_payload = {
        "lat": 28.5525,
        "lon": 77.2030,
        "type": "poor_lighting",
        "note": "Pitch black dark lane near entrance",
        "incident_time": "2026-10-13 22:00:00"
    }
    st, res = c_asha.req("POST", "/api/incidents", inc_payload)
    print(f"[Asha reports incident] Status {st}, ID={res.get('incident', {}).get('id')}, Status={res.get('incident', {}).get('status')}")
    assert st == 201
    inc_id = res["incident"]["id"]

    # 5. Anti-abuse: duplicate within 50m in last 2h
    st, res = c_asha.req("POST", "/api/incidents", inc_payload)
    print(f"[Asha duplicate report within 50m] Status {st} (Expected 409): {res.get('error')}")
    assert st == 409 and res.get("duplicate_incident_id") == inc_id

    # 6. Anti-abuse: Asha self-vote rejected
    st, res = c_asha.req("POST", f"/api/incidents/{inc_id}/vote", {"lat": 28.5525, "lon": 77.2030, "simulated": True})
    print(f"[Asha self-vote] Status {st} (Expected 400): {res.get('error')}")
    assert st == 400

    # 7. Anti-abuse: Far-away vote rejected (> 500m without simulated)
    st, res = c_riya.req("POST", f"/api/incidents/{inc_id}/vote", {"lat": 28.5800, "lon": 77.2500, "simulated": False})
    print(f"[Riya far-away vote (3+ km)] Status {st} (Expected 400): {res.get('error')}")
    assert st == 400

    # 8. Riya valid vote (Confirmation 2 of 3)
    st, res = c_riya.req("POST", f"/api/incidents/{inc_id}/vote", {"lat": 28.5525, "lon": 77.2030, "simulated": True})
    print(f"[Riya votes] Status {st}, Confirmations={res.get('confirmations')}, Verified={res.get('verified')}")
    assert st == 200 and res.get("confirmations") == 2 and res.get("verified") is False

    # 9. Anti-abuse: Riya duplicate vote rejected
    st, res = c_riya.req("POST", f"/api/incidents/{inc_id}/vote", {"lat": 28.5525, "lon": 77.2030, "simulated": True})
    print(f"[Riya duplicate vote] Status {st} (Expected 400): {res.get('error')}")
    assert st == 400

    # 10. Check /api/incidents/pending: incident is still pending
    st, res = c_asha.req("GET", "/api/incidents/pending")
    pending_ids = [p["id"] for p in res.get("pending_incidents", [])]
    print(f"[/api/incidents/pending] Contains inc_id {inc_id}? {inc_id in pending_ids}")
    assert inc_id in pending_ids

    # 11. Meera votes (Confirmation 3 of 3 -> flips to verified!)
    st, res = c_meera.req("POST", f"/api/incidents/{inc_id}/vote", {"lat": 28.5525, "lon": 77.2030, "simulated": True})
    print(f"[Meera votes] Status {st}, Confirmations={res.get('confirmations')}, NewlyVerified={res.get('newly_verified')}")
    assert st == 200 and res.get("confirmations") == 3 and res.get("verified") is True and res.get("newly_verified") is True

    # 12. Check /api/incidents (public verified map): incident is now present
    st, res = c_asha.req("GET", "/api/incidents?status=verified")
    verified_ids = [p["id"] for p in res.get("incidents", [])]
    print(f"[/api/incidents?status=verified] Contains inc_id {inc_id}? {inc_id in verified_ids}")
    assert inc_id in verified_ids

    # 13. Check /api/incidents/pending: incident is NO LONGER pending
    st, res = c_asha.req("GET", "/api/incidents/pending")
    pending_ids = [p["id"] for p in res.get("pending_incidents", [])]
    print(f"[/api/incidents/pending after verification] Contains inc_id {inc_id}? {inc_id in pending_ids}")
    assert inc_id not in pending_ids

    print("=" * 65)
    print("ALL PHASE 2 BACKEND TESTS PASSED! ✅")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
