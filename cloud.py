"""The website version (GitHub Pages), explained in the README.

    python cloud.py push    lock your Mac's database, then commit and upload it to GitHub
    python cloud.py pull    download the latest data GitHub has synced, onto your Mac
    python cloud.py build   build the website into _site/ (to try it on your Mac)
    python cloud.py sync    what GitHub runs every day: sync from Strava, then build

Everything that goes online is locked with SITE_PASSWORD (from .env on your Mac, or a
GitHub secret online). Without the password the files are unreadable.
"""
import base64
import gzip
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import app as webapp          # loads .env, and gives us the stats / PB code
import analysis
import db

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")            # committed to git – locked
STATE_DB = os.path.join(STATE_DIR, "running.db.locked")
STATE_FINGERPRINT = os.path.join(STATE_DIR, "fingerprint")
SITE_SALT = os.path.join(STATE_DIR, "site-salt")   # not secret; keeps "remember me" working
SITE_DIR = os.path.join(HERE, "_site")             # built website – not committed
ITERATIONS = 600_000                               # makes guessing passwords slow
# Optional privacy zone: hide the route within this many metres of every start/finish.
# 0 = off (the full route is shown). Set it in .github/workflows/sync.yml to turn it on.
PRIVACY_RADIUS_M = int(os.environ.get("PRIVACY_RADIUS_M", "0"))


# ---------------------------------------------------------------- locking

def password():
    pw = os.environ.get("SITE_PASSWORD", "")
    if len(pw) < 12:
        sys.exit("SITE_PASSWORD is missing or shorter than 12 characters. "
                 "Add a long password to .env (and as a GitHub secret) – see the README.")
    return pw


def derive_key(pw, salt):
    return PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(pw.encode())


def b64(data):
    return base64.b64encode(data).decode()


def lock(key, plain_bytes):
    """gzip, then AES-GCM – the browser undoes this with the same password."""
    iv = os.urandom(12)
    return {"iv": b64(iv), "ct": b64(AESGCM(key).encrypt(iv, gzip.compress(plain_bytes, 6), None))}


def unlock(key, box):
    return gzip.decompress(AESGCM(key).decrypt(base64.b64decode(box["iv"]), base64.b64decode(box["ct"]), None))


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"))


# ---------------------------------------------------------------- the database, locked in git

def state_fingerprint(key):
    """Changes only when something worth saving changes (new runs, tags, login), so
    GitHub doesn't make a new commit every single day."""
    with db.connect() as conn:
        acts = conn.execute("SELECT id, name, tag, streams_status FROM activities ORDER BY id").fetchall()
        meta = conn.execute("SELECT key, value FROM meta WHERE key != 'last_sync' ORDER BY key").fetchall()
    auth = db.get_auth() or {}
    blob = json.dumps([[list(a) for a in acts], [list(m) for m in meta], auth.get("refresh_token")])
    return hmac.new(key, blob.encode(), hashlib.sha256).hexdigest()


def lock_state(pw, force=False):
    """Save the database into state/ (locked), if it changed. Returns True if it did."""
    salt = os.urandom(16)
    key = derive_key(pw, salt)
    fp_key = derive_key(pw, b"fingerprint-salt")
    fp = state_fingerprint(fp_key)
    if not force and os.path.exists(STATE_FINGERPRINT) and open(STATE_FINGERPRINT).read().strip() == fp:
        return False
    with open(db.DB_PATH, "rb") as f:
        box = lock(key, f.read())
    box.update(salt=b64(salt), iter=ITERATIONS)
    write_json(STATE_DB, box)
    with open(STATE_FINGERPRINT, "w") as f:
        f.write(fp + "\n")
    return True


def unlock_state(pw):
    if not os.path.exists(STATE_DB):
        sys.exit("No saved data in state/ yet. On your Mac, run:  python cloud.py push")
    with open(STATE_DB) as f:
        box = json.load(f)
    key = derive_key(pw, base64.b64decode(box["salt"]))
    try:
        data = unlock(key, box)
    except Exception:
        sys.exit("Couldn't unlock state/running.db.locked – is SITE_PASSWORD the same one you used before?")
    os.makedirs(db.DATA_DIR, exist_ok=True)
    with open(db.DB_PATH, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------- privacy zones

def privacy_centres(conn_runs):
    """Every start and finish point, so home (or anywhere you often start) is never shown."""
    centres = []
    for run_id in conn_runs:
        latlng = [p for p in (db.get_streams(run_id).get("latlng") or []) if p]
        for p in (latlng[:1] + latlng[-1:]):
            if all(analysis.haversine(p, c) > 100 for c in centres):
                centres.append(p)
    return centres


def hide(point, centres):
    if not point or point[0] is None:
        return False
    return any(analysis.haversine(point, c) <= PRIVACY_RADIUS_M for c in centres)


def apply_privacy(detail, compare, centres):
    if detail.get("route"):
        detail["route"] = [None if hide(p, centres) else p for p in detail["route"]]
        visible = [p for p in detail["route"] if p]
        detail["km_markers"] = [m for m in detail.get("km_markers", []) if not hide(m["latlng"], centres)]
        if visible:
            detail["start"], detail["finish"] = visible[0], visible[-1]
            detail["privacy_m"] = PRIVACY_RADIUS_M
        else:
            detail.update(route=None, start=None, finish=None, has_gps=False, route_hidden=True)
    if detail.get("series", {}).get("latlng"):
        detail["series"]["latlng"] = [None if hide(p, centres) else p for p in detail["series"]["latlng"]]
    if compare and compare.get("latlng"):
        compare["latlng"] = [[None, None] if hide(p, centres) else p for p in compare["latlng"]]


# ---------------------------------------------------------------- building the website

def build(pw):
    db.init()
    payload = webapp.runs_payload()
    runs = payload["runs"]

    if os.path.exists(SITE_SALT):
        salt = base64.b64decode(open(SITE_SALT).read().strip())
    else:
        salt = os.urandom(16)
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(SITE_SALT, "w") as f:
            f.write(b64(salt) + "\n")
    key = derive_key(pw, salt)

    shutil.rmtree(SITE_DIR, ignore_errors=True)
    shutil.copytree(os.path.join(HERE, "static"), os.path.join(SITE_DIR, "static"))
    os.remove(os.path.join(SITE_DIR, "static", "index.html"))
    # Give every stylesheet/script a version tag based on its contents, so phones fetch
    # the new design straight away instead of using a cached copy for up to 10 minutes.
    with open(os.path.join(HERE, "static", "index.html")) as f:
        page = f.read()
    for name in sorted(os.listdir(os.path.join(HERE, "static"))):
        if name.endswith((".css", ".js")) and name != "config.js":
            with open(os.path.join(HERE, "static", name), "rb") as f:
                version = hashlib.sha256(f.read()).hexdigest()[:10]
            page = page.replace(f'"static/{name}"', f'"static/{name}?v={version}"')
    page = page.replace('"static/config.js"', f'"static/config.js?v={os.urandom(4).hex()}"')
    with open(os.path.join(SITE_DIR, "index.html"), "w") as f:
        f.write(page)
    repo = os.environ.get("GITHUB_REPOSITORY")
    config = {"mode": "static"}
    if repo:
        config["workflowUrl"] = f"https://github.com/{repo}/actions/workflows/sync.yml"
    with open(os.path.join(SITE_DIR, "static", "config.js"), "w") as f:
        f.write(f"window.SITE_CONFIG = {json.dumps(config)};\n")
    with open(os.path.join(SITE_DIR, ".nojekyll"), "w") as f:
        f.write("")

    write_json(os.path.join(SITE_DIR, "data", "key.json"), {"salt": b64(salt), "iter": ITERATIONS})

    # Walks and rides start from home too, so their ends count towards the privacy zone
    centres = privacy_centres([r["id"] for r in runs + payload["activities"]
                               if r["streams_status"] == "done"]) if PRIVACY_RADIUS_M > 0 else []
    files = {}
    for r in runs + payload["activities"]:
        if r["streams_status"] != "done":
            continue
        activity, streams = db.get_activity(r["id"]), db.get_streams(r["id"])
        detail = analysis.detail(activity, streams)
        compare = analysis.compare_series(activity, streams)
        if centres:
            apply_privacy(detail, compare, centres)
        # File names are scrambled so they don't reveal your Strava activity ids
        name = hmac.new(key, str(r["id"]).encode(), hashlib.sha256).hexdigest()[:24]
        files[r["id"]] = name
        write_json(os.path.join(SITE_DIR, "data", "runs", f"{name}.json"),
                   lock(key, json.dumps({"detail": detail, "compare": compare}).encode()))

    auth = db.get_auth() or {}
    index = dict(payload, files=files, last_sync=db.get_meta("last_sync"),
                 athlete_name=auth.get("athlete_name"),
                 max_hr=db.get_meta("max_hr", webapp.DEFAULT_MAX_HR))
    write_json(os.path.join(SITE_DIR, "data", "index.json"), lock(key, json.dumps(index).encode()))
    zone = (f"routes hidden within {PRIVACY_RADIUS_M} m of {len(centres)} start/finish points"
            if centres else "full routes shown")
    print(f"Built the website in _site/ with {len(runs)} runs and "
          f"{len(payload['activities'])} other activities ({zone}).")


# ---------------------------------------------------------------- commands

def cmd_sync():
    """Run by GitHub every day (and when you press Update)."""
    pw = password()
    unlock_state(pw)
    db.init()
    manager = webapp.sync_manager
    manager.time_budget_s = 95 * 60   # the workflow is stopped at 120 min – finish and save well before
    manager.start()
    manager._thread.join()
    state = manager.snapshot()
    print("Sync:", state["message"])
    build(pw)
    print("Saved new data." if lock_state(pw) else "No new runs – nothing new to save.")
    if state["error_kind"] in ("auth", "config", "unknown"):
        # Tell the workflow to finish with a red cross, so GitHub emails you
        with open(os.path.join(HERE, "sync_problem.txt"), "w") as f:
            f.write(state["message"] + "\n")


def cmd_push():
    pw = password()
    if not os.path.exists(db.DB_PATH):
        sys.exit("There's no data on this Mac yet – start the app and connect Strava first.")
    db.set_meta("cloud_mode", True)   # from now on the Mac app won't sync by itself
    lock_state(pw, force=True)
    print("Locked your data into state/. Uploading to GitHub…")
    subprocess.run(["git", "add", "state"], cwd=HERE, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE).returncode == 0:
        print("Nothing changed since the last upload.")
        return
    subprocess.run(["git", "commit", "-m", "Update running data from my Mac"], cwd=HERE, check=True)
    subprocess.run(["git", "pull", "--rebase"], cwd=HERE, check=True)
    subprocess.run(["git", "push"], cwd=HERE, check=True)
    print("Done. The website will pick this up the next time it updates.")


def cmd_pull():
    pw = password()
    subprocess.run(["git", "pull", "--rebase"], cwd=HERE, check=True)
    if os.path.exists(db.DB_PATH):
        backup = db.DB_PATH + ".before-pull"
        shutil.copy(db.DB_PATH, backup)
        print(f"Kept a copy of your old Mac data in {os.path.relpath(backup, HERE)}")
    unlock_state(pw)
    db.set_meta("cloud_mode", True)
    print("Your Mac now has the same data as the website.")


if __name__ == "__main__":
    commands = {"sync": cmd_sync, "push": cmd_push, "pull": cmd_pull, "build": lambda: build(password())}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()
