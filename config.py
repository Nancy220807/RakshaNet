"""
config.py — All tunables for RakshaNet in one place.
Change values here; nowhere else needs to be touched.
"""

# ── Area of interest ────────────────────────────────────────────────────────
CENTER_LAT  = 28.5494      # Hauz Khas, New Delhi
CENTER_LON  = 77.2001
RADIUS_M    = 2000         # metres from centre to download

# ── Safety-score weights (must sum to 1.0) ──────────────────────────────────
W_LIGHTING  = 0.40
W_CROWD     = 0.25
W_INCIDENT  = 0.25
W_TIME      = 0.10

# ── Lighting base values by OSM highway type (0-100) ────────────────────────
LIGHTING_BY_TYPE = {
    "primary":        80,
    "secondary":      75,
    "tertiary":       65,
    "residential":    50,
    "service":        35,
    "footway":        30,
    "path":           25,
    "cycleway":       30,
    "unclassified":   45,
    "living_street":  55,
    "trunk":          85,
    "motorway":       90,   # shouldn't appear in walk network but just in case
}
LIGHTING_DEFAULT = 40      # fallback for unknown types
LIGHTING_NOISE   = 10      # ±noise added via seeded random

# ── Crowd base values by highway type (0-100) ────────────────────────────────
CROWD_BY_TYPE = {
    "primary":        80,
    "secondary":      70,
    "tertiary":       60,
    "residential":    35,
    "service":        25,
    "footway":        45,
    "path":           20,
    "cycleway":       30,
    "unclassified":   40,
    "living_street":  50,
    "trunk":          75,
}
CROWD_DEFAULT = 35

# ── Hourly crowd multiplier (index = hour 0-23) ──────────────────────────────
# Peaks 8-10 and 17-20; near-zero 0-5; sharp drop after 21:00
CROWD_HOURLY_MULTIPLIER = [
    0.05,  # 0
    0.05,  # 1
    0.05,  # 2
    0.05,  # 3
    0.05,  # 4
    0.10,  # 5
    0.30,  # 6
    0.70,  # 7
    0.95,  # 8  ← morning peak
    1.00,  # 9
    0.90,  # 10
    0.75,  # 11
    0.80,  # 12
    0.70,  # 13
    0.65,  # 14
    0.75,  # 15
    0.90,  # 16
    1.00,  # 17 ← evening peak
    0.95,  # 18
    0.85,  # 19
    0.60,  # 20
    0.35,  # 21
    0.15,  # 22
    0.08,  # 23
]

# ── Time-of-day factor (index = hour 0-23, 0-100) ───────────────────────────
TIME_FACTOR_BY_HOUR = [
    20,  # 0
    20,  # 1
    20,  # 2
    20,  # 3
    25,  # 4
    35,  # 5
    60,  # 6
    85,  # 7
    90,  # 8
    95,  # 9
    95,  # 10
    95,  # 11
    95,  # 12
    95,  # 13
    90,  # 14
    90,  # 15
    90,  # 16
    95,  # 17
    92,  # 18
    85,  # 19
    70,  # 20
    50,  # 21
    35,  # 22
    25,  # 23
]

# ── Incident seeding ─────────────────────────────────────────────────────────
INCIDENT_SEED        = 42
NUM_INCIDENTS        = 25          # how many verified incidents to seed
INCIDENT_PENALTY     = 18         # score penalty per verified incident (capped at 80)
INCIDENT_RADIUS_M    = 80         # snap incident to nearest edge within this range (metres)

# ── Routing ──────────────────────────────────────────────────────────────────
ROUTING_K            = 3.0        # cost multiplier for unsafe edges
DETOUR_CAP           = 1.5        # safest route ≤ 1.5× shortest distance
K_STEP               = 0.5        # reduce K by this if detour cap exceeded

# ── Score color bands ────────────────────────────────────────────────────────
BAND_SAFE             = 70        # ≥ 70 → green
BAND_CAUTION          = 40        # 40-69 → orange
                                   # < 40 → red

# ── Walking speed ────────────────────────────────────────────────────────────
WALK_SPEED_KMH        = 5.0

# ── Phase 2 tunables ────────────────────────────────────────────────────────
DEMO_MODE            = True        # Allows "Acting as" user switcher & simulated location
VERIFY_THRESHOLD     = 3           # Confirming users needed (reporter + 2 community votes)
VOTE_RADIUS_M        = 500         # Voter must be within 500m of incident

# ── Demo sample & preset routes ─────────────────────────────────────────────
SAMPLE_START = (28.5433, 77.2065)   # Hauz Khas Village entrance area
SAMPLE_END   = (28.5592, 77.2090)   # Green Park Metro Station area

DEMO_ROUTES = [
    {
        "id": "hkv_to_metro",
        "name": "Hauz Khas Village → Green Park Metro",
        "start": (28.5433, 77.2065),
        "end": (28.5592, 77.2090),
    },
    {
        "id": "safdarjung_to_deerpark",
        "name": "Safdarjung Enclave → Deer Park",
        "start": (28.5540, 77.2070),
        "end": (28.5470, 77.2000),
    },
    {
        "id": "deerpark_to_safdarjung",
        "name": "Deer Park Lake → Safdarjung Club",
        "start": (28.5450, 77.2010),
        "end": (28.5530, 77.2030),
    },
]

# ── Paths ────────────────────────────────────────────────────────────────────
import os
BASE_DIR   = os.path.dirname(__file__)
DATA_DIR   = os.path.join(BASE_DIR, "data")
GRAPH_PATH = os.path.join(DATA_DIR, "graph.graphml")
DB_PATH    = os.path.join(DATA_DIR, "rakshanet.db")

# ── Phase 3: Smart SOS ───────────────────────────────────────────────────────
SOS_ENABLED                 = True
SOS_PROVIDER                = os.environ.get("SOS_PROVIDER", "mock")
SOS_COUNTDOWN_SECONDS       = 5
SOS_MAX_CONTACTS            = 3
SOS_COOLDOWN_SECONDS        = 30
SOS_MAX_PER_DAY             = 5
RESPONDER_RADIUS_M          = 800
SOS_LOCATION_UPDATE_SECONDS = 5
TRACK_LINK_TTL_HOURS        = 6

# Twilio Secrets (env vars / .env)
TWILIO_ACCOUNT_SID          = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN           = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER          = os.environ.get("TWILIO_FROM_NUMBER", "")

