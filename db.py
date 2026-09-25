"""Everything to do with the local SQLite database (data/running.db).

The database holds your runs, their detailed streams, your Strava login
tokens and a few settings. It lives only on your computer.
"""
import json
import os
import sqlite3
import threading

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "running.db")

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    athlete_id    INTEGER,
    athlete_name  TEXT,
    access_token  TEXT,
    refresh_token TEXT,
    expires_at    INTEGER,
    scope         TEXT
);

CREATE TABLE IF NOT EXISTS activities (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT,
    sport_type           TEXT,
    start_date           TEXT,     -- UTC, ISO 8601
    start_epoch          INTEGER,  -- UTC seconds, handy for sorting
    start_date_local     TEXT,     -- your local clock time
    timezone             TEXT,
    summary_distance     REAL,     -- metres, as reported by Strava (not used for stats)
    moving_time          INTEGER,
    elapsed_time         INTEGER,
    total_elevation_gain REAL,
    average_heartrate    REAL,
    max_heartrate        REAL,
    trainer              INTEGER,  -- 1 = treadmill / indoor
    private              INTEGER,
    has_gps              INTEGER,  -- filled in once streams arrive
    has_hr               INTEGER,
    streams_status       TEXT DEFAULT 'pending',  -- pending | done | missing
    streams_note         TEXT,
    tag                  TEXT,     -- your own label, e.g. "easy"
    summary_json         TEXT,
    fetched_at           TEXT
);

CREATE TABLE IF NOT EXISTS streams (
    activity_id INTEGER PRIMARY KEY REFERENCES activities(id),
    data_json   TEXT
);

CREATE TABLE IF NOT EXISTS run_stats (
    activity_id INTEGER PRIMARY KEY REFERENCES activities(id),
    version     INTEGER,
    data_json   TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)


# ---------- small key/value store for settings and sync progress ----------

def get_meta(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_meta(key, value):
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


# ---------- Strava login tokens ----------

def get_auth():
    with connect() as conn:
        row = conn.execute("SELECT * FROM auth WHERE id = 1").fetchone()
    return dict(row) if row else None


def save_auth(access_token, refresh_token, expires_at, scope=None,
              athlete_id=None, athlete_name=None):
    with _lock, connect() as conn:
        existing = conn.execute("SELECT * FROM auth WHERE id = 1").fetchone()
        if existing:
            conn.execute(
                "UPDATE auth SET access_token = ?, refresh_token = ?, expires_at = ?, "
                "scope = COALESCE(?, scope), athlete_id = COALESCE(?, athlete_id), "
                "athlete_name = COALESCE(?, athlete_name) WHERE id = 1",
                (access_token, refresh_token, expires_at, scope, athlete_id, athlete_name),
            )
        else:
            conn.execute(
                "INSERT INTO auth (id, access_token, refresh_token, expires_at, scope, "
                "athlete_id, athlete_name) VALUES (1, ?, ?, ?, ?, ?, ?)",
                (access_token, refresh_token, expires_at, scope, athlete_id, athlete_name),
            )


def clear_auth():
    with _lock, connect() as conn:
        conn.execute("DELETE FROM auth")


# ---------- runs ----------

def upsert_activity(a):
    """Save a run summary from Strava. Keeps your tag and any streams already fetched.

    Returns True if this run wasn't in the database before."""
    start_epoch = utc_epoch(a["start_date"])
    values = (
        a["id"], a.get("name"), a.get("sport_type") or a.get("type"),
        a["start_date"], start_epoch, a.get("start_date_local"), a.get("timezone"),
        a.get("distance"), a.get("moving_time"), a.get("elapsed_time"),
        a.get("total_elevation_gain"), a.get("average_heartrate"), a.get("max_heartrate"),
        1 if a.get("trainer") else 0, 1 if a.get("private") else 0,
        json.dumps(a),
    )
    with _lock, connect() as conn:
        is_new = conn.execute("SELECT 1 FROM activities WHERE id = ?",
                              (a["id"],)).fetchone() is None
        conn.execute(
            """INSERT INTO activities (id, name, sport_type, start_date, start_epoch,
                   start_date_local, timezone, summary_distance, moving_time, elapsed_time,
                   total_elevation_gain, average_heartrate, max_heartrate, trainer, private,
                   summary_json, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                   name = excluded.name, sport_type = excluded.sport_type,
                   start_date = excluded.start_date, start_epoch = excluded.start_epoch,
                   start_date_local = excluded.start_date_local, timezone = excluded.timezone,
                   summary_distance = excluded.summary_distance,
                   moving_time = excluded.moving_time, elapsed_time = excluded.elapsed_time,
                   total_elevation_gain = excluded.total_elevation_gain,
                   average_heartrate = excluded.average_heartrate,
                   max_heartrate = excluded.max_heartrate, trainer = excluded.trainer,
                   private = excluded.private, summary_json = excluded.summary_json""",
            values,
        )
    return is_new


def utc_epoch(iso_z):
    from datetime import datetime, timezone
    return int(datetime.strptime(iso_z, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp())


def newest_start_epoch():
    with connect() as conn:
        row = conn.execute("SELECT MAX(start_epoch) AS m FROM activities").fetchone()
    return row["m"]


def pending_stream_ids():
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM activities WHERE streams_status = 'pending' "
            "ORDER BY start_epoch DESC").fetchall()
    return [r["id"] for r in rows]


def save_streams(activity_id, streams, note=None):
    has_gps = 1 if streams.get("latlng") else 0
    has_hr = 1 if streams.get("heartrate") else 0
    with _lock, connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO streams (activity_id, data_json) VALUES (?, ?)",
            (activity_id, json.dumps(streams, separators=(",", ":"))),
        )
        conn.execute(
            "UPDATE activities SET streams_status = 'done', has_gps = ?, has_hr = ?, "
            "streams_note = ? WHERE id = ?",
            (has_gps, has_hr, note, activity_id),
        )


def mark_streams_missing(activity_id, note):
    with _lock, connect() as conn:
        conn.execute(
            "UPDATE activities SET streams_status = 'missing', streams_note = ?, "
            "has_gps = 0, has_hr = 0 WHERE id = ?",
            (note, activity_id),
        )


def counts():
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS runs, "
            "SUM(streams_status = 'pending') AS pending, "
            "SUM(streams_status = 'missing') AS missing FROM activities").fetchone()
    return {"runs": row["runs"] or 0, "pending": row["pending"] or 0,
            "missing": row["missing"] or 0}


def list_runs_basic(limit=500):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, sport_type, start_date_local, summary_distance, moving_time, "
            "trainer, private, has_gps, has_hr, streams_status, streams_note, tag "
            "FROM activities ORDER BY start_epoch DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def delete_activity(activity_id):
    """Forget a run that's been deleted on Strava (or is no longer a run)."""
    with _lock, connect() as conn:
        for table, col in (("run_stats", "activity_id"), ("streams", "activity_id"), ("activities", "id")):
            conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (activity_id,))


def ids_since(start_epoch):
    with connect() as conn:
        rows = conn.execute("SELECT id FROM activities WHERE start_epoch >= ?", (start_epoch,)).fetchall()
    return {r["id"] for r in rows}


def get_activity(activity_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()
    return dict(row) if row else None


def get_streams(activity_id):
    with connect() as conn:
        row = conn.execute("SELECT data_json FROM streams WHERE activity_id = ?",
                           (activity_id,)).fetchone()
    return json.loads(row["data_json"]) if row else {}


def stats_versions():
    """{activity_id: version} for every run that has stats saved."""
    with connect() as conn:
        rows = conn.execute("SELECT activity_id, version FROM run_stats").fetchall()
    return {r["activity_id"]: r["version"] for r in rows}


def save_stats(activity_id, version, stats):
    with _lock, connect() as conn:
        conn.execute("INSERT OR REPLACE INTO run_stats (activity_id, version, data_json) "
                     "VALUES (?, ?, ?)", (activity_id, version, json.dumps(stats)))


def list_runs_with_stats():
    with connect() as conn:
        rows = conn.execute(
            "SELECT a.id, a.name, a.sport_type, a.start_date_local, a.start_epoch, a.trainer, "
            "a.private, a.has_gps, a.has_hr, a.streams_status, a.streams_note, a.tag, "
            "a.summary_distance, a.moving_time, a.elapsed_time, a.total_elevation_gain, "
            "a.average_heartrate, a.max_heartrate, s.data_json AS stats "
            "FROM activities a LEFT JOIN run_stats s ON s.activity_id = a.id "
            "ORDER BY a.start_epoch DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["stats"] = json.loads(d["stats"]) if d["stats"] else None
        out.append(d)
    return out


def set_tag(activity_id, tag):
    with _lock, connect() as conn:
        conn.execute("UPDATE activities SET tag = ? WHERE id = ?", (tag or None, activity_id))


def all_tags():
    with connect() as conn:
        rows = conn.execute("SELECT DISTINCT tag FROM activities WHERE tag IS NOT NULL "
                            "ORDER BY tag").fetchall()
    return [r["tag"] for r in rows]
