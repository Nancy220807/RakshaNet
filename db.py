"""
db.py — Thin SQLite helpers for RakshaNet.

Schema is defined here; Phase 2 features (incidents reporting, SOS contacts,
users) plug in by adding tables without touching routing or scoring code.
"""

import sqlite3
import logging
from contextlib import contextmanager

from config import DB_PATH

logger = logging.getLogger(__name__)


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Edge safety attributes (lighting, crowd base) seeded once at startup.
-- u, v, key match OSMnx MultiDiGraph edge keys.
CREATE TABLE IF NOT EXISTS edge_attrs (
    u           INTEGER NOT NULL,
    v           INTEGER NOT NULL,
    key         INTEGER NOT NULL DEFAULT 0,
    lighting    REAL    NOT NULL,   -- 0-100 static lighting score
    crowd_base  REAL    NOT NULL,   -- 0-100 base crowd (multiplied by hourly curve at runtime)
    highway     TEXT,               -- OSM highway tag (informational)
    PRIMARY KEY (u, v, key)
);

-- Incidents table.  Phase 2 will add INSERT and status-update endpoints;
-- the scoring engine already reads from this table.
CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lat         REAL    NOT NULL,
    lon         REAL    NOT NULL,
    edge_u      INTEGER,            -- snapped edge (populated at seed time or report time)
    edge_v      INTEGER,
    edge_key    INTEGER DEFAULT 0,
    itype       TEXT    NOT NULL DEFAULT 'other',   -- harassment|poor_lighting|unsafe_area|other
    status      TEXT    NOT NULL DEFAULT 'pending', -- pending|verified
    upvotes     INTEGER NOT NULL DEFAULT 0,
    reported_at TEXT    NOT NULL DEFAULT (datetime('now')),
    description TEXT
);

-- Phase 2 placeholder: trusted SOS contacts per user.
CREATE TABLE IF NOT EXISTS sos_contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    phone       TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Phase 2 placeholder: registered users / volunteers.
CREATE TABLE IF NOT EXISTS users (
    id          TEXT    PRIMARY KEY,   -- UUID
    name        TEXT,
    phone       TEXT,
    is_volunteer INTEGER NOT NULL DEFAULT 0,
    lat         REAL,                  -- last known location (volunteer)
    lon         REAL,
    updated_at  TEXT
);
"""

# ── Connection factory ────────────────────────────────────────────────────────

def get_connection():
    """Open a new SQLite connection. Caller is responsible for closing it."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_conn():
    """Context manager: yields a connection that auto-commits on clean exit."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_db(conn):
    """Additive migration: inspect schema and add missing columns/tables without dropping anything."""
    # 1. users table
    cursor = conn.execute("PRAGMA table_info(users)")
    existing_user_cols = {row["name"] for row in cursor.fetchall()}
    if "email" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "password_hash" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if "created_at" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN created_at TEXT")

    # 2. incidents table
    cursor = conn.execute("PRAGMA table_info(incidents)")
    existing_inc_cols = {row["name"] for row in cursor.fetchall()}
    if "reporter_id" not in existing_inc_cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN reporter_id TEXT")
    if "incident_time" not in existing_inc_cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN incident_time TEXT")
    if "note" not in existing_inc_cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN note TEXT")
    if "verified_at" not in existing_inc_cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN verified_at TEXT")
    if "edge_id" not in existing_inc_cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN edge_id TEXT")
    if "created_at" not in existing_inc_cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN created_at TEXT")

    # 3. incident_votes table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS incident_votes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id INTEGER NOT NULL,
        user_id     TEXT NOT NULL,
        voter_lat   REAL,
        voter_lon   REAL,
        distance_m  REAL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(incident_id, user_id)
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incident_votes_inc ON incident_votes(incident_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);")

    # 4. Phase 3: users responder columns
    if "is_responder" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_responder INTEGER DEFAULT 0")
    if "responder_verified" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN responder_verified INTEGER DEFAULT 0")
    if "responder_consent_at" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN responder_consent_at TEXT")

    # 5. Phase 3: trusted_contacts
    conn.execute("""
    CREATE TABLE IF NOT EXISTS trusted_contacts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL,
        name        TEXT NOT NULL,
        phone       TEXT NOT NULL,
        relation    TEXT DEFAULT 'Other',
        created_at  TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, phone),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_user ON trusted_contacts(user_id);")

    # 6. Phase 3: responder_locations
    conn.execute("""
    CREATE TABLE IF NOT EXISTS responder_locations (
        user_id     TEXT PRIMARY KEY,
        lat         REAL NOT NULL,
        lon         REAL NOT NULL,
        updated_at  TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)

    # 7. Phase 3: sos_alerts
    conn.execute("""
    CREATE TABLE IF NOT EXISTS sos_alerts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL,
        status      TEXT NOT NULL,
        start_lat   REAL NOT NULL,
        start_lon   REAL NOT NULL,
        last_lat    REAL,
        last_lon    REAL,
        street_name TEXT,
        track_token TEXT UNIQUE,
        created_at  TEXT DEFAULT (datetime('now')),
        resolved_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sos_user ON sos_alerts(user_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sos_status ON sos_alerts(status);")

    # 8. Phase 3: sos_locations
    conn.execute("""
    CREATE TABLE IF NOT EXISTS sos_locations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id    INTEGER NOT NULL,
        lat         REAL NOT NULL,
        lon         REAL NOT NULL,
        created_at  TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(alert_id) REFERENCES sos_alerts(id)
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sos_loc_alert ON sos_locations(alert_id);")

    # 9. Phase 3: sos_notifications
    conn.execute("""
    CREATE TABLE IF NOT EXISTS sos_notifications (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id       INTEGER NOT NULL,
        channel        TEXT NOT NULL,
        recipient_type TEXT NOT NULL,
        recipient_ref  TEXT NOT NULL,
        status         TEXT NOT NULL,
        error          TEXT,
        created_at     TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(alert_id) REFERENCES sos_alerts(id)
    );
    """)

    # 10. Phase 3: notifications (in-app responder inbox)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      TEXT NOT NULL,
        type         TEXT NOT NULL,
        payload_json TEXT,
        read         INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, read);")

    # 11. Phase 3: ended_tracks
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ended_tracks (
        token_hash  TEXT PRIMARY KEY,
        alert_id    INTEGER,
        outcome     TEXT,
        ended_at    TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ended_tracks ON ended_tracks(token_hash);")


DEMO_USERS = [
    {"id": "user_asha",  "name": "Asha",  "email": "asha@rakshanet.in",  "phone": "9876543210"},
    {"id": "user_riya",  "name": "Riya",  "email": "riya@rakshanet.in",  "phone": "9876543211"},
    {"id": "user_meera", "name": "Meera", "email": "meera@rakshanet.in", "phone": "9876543212"},
    {"id": "user_kabir", "name": "Kabir", "email": "kabir@rakshanet.in", "phone": "9876543213"},
    {"id": "user_zoya",  "name": "Zoya",  "email": "zoya@rakshanet.in",  "phone": "9876543214"},
]
DEMO_PASSWORD = "raksha2026"


def seed_demo_users(conn):
    """Seed 5 demo users if they don't already exist."""
    from werkzeug.security import generate_password_hash
    pw_hash = generate_password_hash(DEMO_PASSWORD)
    for u in DEMO_USERS:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (u["id"],)).fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO users (id, name, email, phone, password_hash, is_volunteer)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (u["id"], u["name"], u["email"], u["phone"], pw_hash),
            )
    logger.info("Demo users verified/seeded.")


DEMO_CONTACTS = [
    ("user_asha",  "Riya Sharma",   "+919876543211", "Sister"),
    ("user_asha",  "Meera Patel",   "+919876543212", "Friend"),
    ("user_riya",  "Asha Sharma",   "+919876543210", "Sister"),
    ("user_riya",  "Mom (Sunita)",  "+919876543220", "Parent"),
    ("user_meera", "Kabir Patel",   "+919876543213", "Brother"),
    ("user_meera", "Asha Sharma",   "+919876543210", "Friend"),
    ("user_kabir", "Meera Patel",   "+919876543212", "Sister"),
    ("user_kabir", "Dad (Rajesh)",  "+919876543230", "Parent"),
    ("user_zoya",  "Asha Sharma",   "+919876543210", "Friend"),
    ("user_zoya",  "Riya Sharma",   "+919876543211", "Friend"),
]

DEMO_RESPONDERS = [
    ("user_meera", 28.5520, 77.2040),  # near Hauz Khas / Green Park
    ("user_zoya",  28.5480, 77.2020),  # near Deer Park
    ("user_asha",  28.5560, 77.2060),  # near Green Park Metro
]


def seed_phase3_data(conn):
    """Idempotently seed demo contacts and verified community responders."""
    # 1. Seed demo contacts
    for uid, name, phone, rel in DEMO_CONTACTS:
        conn.execute(
            """
            INSERT OR IGNORE INTO trusted_contacts (user_id, name, phone, relation)
            VALUES (?, ?, ?, ?)
            """,
            (uid, name, phone, rel),
        )

    # 2. Seed verified responders
    for uid, lat, lon in DEMO_RESPONDERS:
        conn.execute(
            """
            UPDATE users
            SET is_responder = 1, responder_verified = 1, responder_consent_at = datetime('now')
            WHERE id = ?
            """,
            (uid,),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO responder_locations (user_id, lat, lon, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (uid, lat, lon),
        )
    logger.info("Phase 3 demo contacts and responders verified/seeded.")


def init_db():
    """Create tables if they don't exist and run additive migrations. Safe to call multiple times."""
    import os
    os.makedirs(DB_PATH.rsplit("/", 1)[0] if "/" in DB_PATH else DB_PATH.rsplit("\\", 1)[0], exist_ok=True)
    with db_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        migrate_db(conn)
        seed_demo_users(conn)
        seed_phase3_data(conn)
    logger.info("Database initialised and migrated at %s", DB_PATH)


# ── Edge attrs helpers ────────────────────────────────────────────────────────

def upsert_edge_attrs(conn, rows):
    """
    Insert or replace edge attribute rows.
    rows: list of dicts with keys u, v, key, lighting, crowd_base, highway.
    """
    conn.executemany(
        """
        INSERT OR REPLACE INTO edge_attrs (u, v, key, lighting, crowd_base, highway)
        VALUES (:u, :v, :key, :lighting, :crowd_base, :highway)
        """,
        rows,
    )


def load_edge_attrs(conn):
    """Return all edge attrs as a dict keyed by (u, v, key)."""
    rows = conn.execute("SELECT u, v, key, lighting, crowd_base, highway FROM edge_attrs").fetchall()
    return {(r["u"], r["v"], r["key"]): dict(r) for r in rows}


# ── Incident helpers ──────────────────────────────────────────────────────────

def insert_incident(conn, lat, lon, edge_u, edge_v, edge_key, itype, status, upvotes, reported_at, description=""):
    conn.execute(
        """
        INSERT INTO incidents (lat, lon, edge_u, edge_v, edge_key, itype, status, upvotes, reported_at, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (lat, lon, edge_u, edge_v, edge_key, itype, status, upvotes, reported_at, description),
    )


def load_verified_incidents(conn, days=30):
    """
    Return verified incidents from the last `days` days as a list of dicts.
    """
    rows = conn.execute(
        """
        SELECT id, lat, lon, edge_u, edge_v, edge_key, itype, upvotes, reported_at, incident_time, note, description
        FROM incidents
        WHERE status = 'verified'
          AND (reported_at >= datetime('now', ?) OR incident_time >= datetime('now', ?))
        """,
        (f"-{days} days", f"-{days} days"),
    ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("DB ready at", DB_PATH)
