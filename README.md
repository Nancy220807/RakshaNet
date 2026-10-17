# RakshaNet 🛡️
### Women's Safety Route Planner & Community Verification — DevDiva Jam 2026

> *"Pick the safest route, not just the fastest. Powered by real-time community verification."*

RakshaNet is a web app that helps women commuting alone in Delhi find the **safest walking route**, not just the shortest one. Each street segment is color-coded green / orange / red based on a real-time safety score that accounts for lighting, crowd density, verified incidents, and — crucially — the **time of day**. A road that's safe at 6 PM can turn red at 11 PM.

In **Phase 2**, RakshaNet introduces **community hazard reporting and peer verification**: women report hazards (poor lighting, harassment, unsafe deserted lanes), nearby community members verify them, and once verified, RakshaNet dynamically penalizes the road segment in real time without restarting the server.

---

## ✨ Features

### Phase 1: Safe Route Planner
| Feature | Details |
|---------|---------|
| **Safe Route Planner** | Shortest vs Safest route, side-by-side on a Leaflet map |
| **Typeable Autocomplete Search** | Instant prefix matching on local OSM streets + Nominatim fallback |
| **3 Divergent Demo Presets** | Hauz Khas Village → Green Park Metro, Safdarjung Enclave → Deer Park, Deer Park Lake → Safdarjung Club |
| **Per-segment scoring** | 0-100 score = 40% lighting + 25% crowd + 25% incidents + 10% time factor |
| **Time-of-day toggle** | 6 PM / 9 PM / 11 PM quick-select + 0-23h slider — instantly recomputes |
| **Color-coded segments** | 🟢 Safe ≥70 · 🟠 Caution 40-69 · 🔴 Avoid <40 |
| **Comparison panel** | Distance, ETA, avg score, min score, risky segment count, and clear verdict |
| **Safety heatmap** | Toggle overlay showing safety rating for all streets in the area |

### Phase 2: Community Verification & Dynamic Scoring
| Feature | Details |
|---------|---------|
| **Incident Reporting** | Report 4 hazard types (`poor_lighting`, `harassment`, `unsafe_area`, `other`) with map picker & time |
| **Smart Snapping** | Snaps coordinates to nearest OSM road edge and displays human-readable street names |
| **Anti-Abuse Engine** | Rate-limited (max 5 reports/hour), 50m / 2hr duplicate detection (HTTP 409), self-vote rejection |
| **Peer Verification** | Requires 3 community confirmations (`VERIFY_THRESHOLD=3`) to go live; 500m proximity check |
| **Dynamic Scoring** | Verified hazards immediately penalize edge scores; `poor_lighting` cuts lighting factor; ±3h time window |
| **Community Feed & Alerts** | Tabbed interface with pending hazard alerts and a verified safety feed with filters |
| **Demo Mode & Auth** | Seeded accounts (Asha, Riya, Meera, Kabir, Zoya) with instant switcher and simulated proximity |

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+
- pip
- Internet access (first run only — to download OSM walk graph)

### 2. Install dependencies
```bash
cd RakshaNet
pip install -r requirements.txt
```

### 3. Run the app
```bash
python app.py
```

Open your browser at **http://localhost:5000** 🎉

---

## 🧪 Testing Suite

RakshaNet includes a full pytest suite covering community verification, anti-abuse guards, and dynamic scoring:

```bash
python -m pytest tests/test_phase2.py -v
```

**Test Coverage:**
- `test_self_vote_rejected` — reporter cannot vote on own report
- `test_duplicate_vote_rejected` — same user cannot vote twice
- `test_far_away_vote_rejected` — votes outside 500m are rejected (unless simulated in DEMO_MODE)
- `test_threshold_flips_status_exactly_at_configured_count` — flips to verified at 3 confirmations
- `test_pending_incidents_do_not_affect_scores` — pending reports never drop road scores
- `test_verification_changes_edge_score` — verified incident drops edge safety score
- `test_rate_limit_enforced` — maximum 5 reports per user per hour
- `test_duplicate_report_detection` — reports within 50m and 2 hours return HTTP 409

---

## 🎬 Live Hackathon Demo (for judges)

You can run the automated end-to-end demo script or follow the interactive web steps:

### Automated Script
```bash
python demo_phase2.py
```

### Interactive Web UI Demo (2 Minutes)
1. **Open http://localhost:5000** in browser (maximize window).
2. **Top Bar**: Notice active user **Asha** and the `🌱 Demo data: seeded` badge.
3. **Route Tab**: Click the preset **"Hauz Khas Village → Green Park Metro"**.
   - Note the baseline route at 23:00.
4. **Report Tab**:
   - Asha spots broken streetlights on an inner park lane.
   - Select **"Poor Lighting"**, pick location near Deer Park, add note: *"Broken streetlights, pitch black"*, and click **"Submit Report"**.
   - Alert appears: *"Incident reported. Needs 2 more confirmations to go live."*
5. **Verify & Feed Tab**:
   - Switch user in top dropdown to **Riya**.
   - In **Pending Verification Alerts**, Riya clicks **"Confirm Hazard"** (confirmations: 2/3).
   - Switch user in top dropdown to **Meera**.
   - Meera clicks **"Confirm Hazard"** (confirmations: 3/3).
   - Card flips to **VERIFIED** and moves to the **Community Safety Feed**!
6. **Route Impact**:
   - Click back to **Route Tab**. Re-run the preset.
   - The verified hazard dynamically penalizes the road segment score, and the route avoids the unlit hazard!

---

## 📁 Project Structure

```
RakshaNet/
├── app.py               # Flask application + API routes + startup sequence
├── config.py            # Weights, BBOX, DEMO_ROUTES, DEMO_MODE, VERIFY_THRESHOLD
├── graph_loader.py      # OSMnx walk graph download + cache logic
├── seed_data.py         # Synthetic seed incidents & edge scores
├── scoring.py           # Multi-factor safety engine + dynamic incremental updater
├── routing.py           # Shortest vs Safest Dijkstra routing with detour cap
├── db.py                # SQLite schema + additive migrations + seed users
├── demo_phase2.py       # End-to-end hackathon demo script
├── tests/
│   └── test_phase2.py   # Pytest suite for Phase 2 verification & scoring
├── data/
│   ├── graph.graphml    # Cached OSM graph
│   └── rakshanet.db     # SQLite database
├── static/
│   ├── index.html       # 3-tab layout: Route, Report, Verify & Feed
│   ├── style.css        # Responsive dark UI (#E91E8C pink accent)
│   └── app.js           # Leaflet map, live search, reporting, and voting
├── requirements.txt
└── README.md
```

---

## 🔧 API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/meta` | GET | Area center, bounding box, demo route presets |
| `GET /api/search?q=<query>` | GET | Fast autocomplete search (OSM index + Nominatim) |
| `GET /api/nearest?lat=&lon=` | GET | Snap coords to nearest street name & edge ID |
| `GET /api/route` | GET | Shortest + safest route computation with safety breakdown |
| `GET /api/segments` | GET | Segment safety scores for heatmap overlay |
| `GET /api/auth/me` | GET | Current session user |
| `POST /api/auth/login` | POST | Login with email and password |
| `POST /api/auth/register` | POST | Register new community account |
| `POST /api/auth/switch` | POST | Instant demo persona switcher (`DEMO_MODE=True`) |
| `POST /api/incidents` | POST | Submit hazard report (snapped, rate-limited, duplicate checked) |
| `GET /api/incidents` | GET | Verified incident feed with hours/radius/type filters |
| `GET /api/incidents/pending` | GET | Pending incidents requiring community verification |
| `POST /api/incidents/<id>/vote` | POST | Confirm/verify incident (proximity checked, threshold flip) |
| `POST /api/sos` | POST | SOS emergency endpoint (501 stub) |

---

## 🧠 Safety Score Model

```
score(edge, hour) =
    0.40 × lighting (penalized by verified poor_lighting reports)
  + 0.25 × crowd_base × hourly_multiplier[hour]
  + 0.25 × max(0, 100 - verified_incident_penalty)
  + 0.10 × time_factor[hour]
```

- **Incident Penalty**: Verified reports apply penalties with a 30-day linear age decay and time-of-day relevance (±3 hours).
- **Lighting Degradation**: Verified `poor_lighting` reports directly reduce the lighting component by 40 points on that street.
- **Anti-Abuse**: Unverified (pending) reports exert 0 penalty.


*Built with ❤️ using Flask · OSMnx · NetworkX · Leaflet.js · SQLite*
