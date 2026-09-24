"""Running dashboard – the small web server that runs on your computer.

Start it with:   python app.py
Then open:       http://localhost:5050
"""
import os
import secrets

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

import analysis  # noqa: E402
import db  # noqa: E402  (needs .env loaded first)
import strava  # noqa: E402
from sync import SyncManager  # noqa: E402

PORT = int(os.environ.get("PORT", 5050))  # Strava must send you back to this same port
REDIRECT_URI = f"http://localhost:{PORT}/auth/callback"

app = Flask(__name__, static_folder=None)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0   # browser always checks for updated page files


def ensure_stats():
    """Work out stats for any run that doesn't have up-to-date ones yet."""
    versions = db.stats_versions()
    for run in db.list_runs_basic(limit=100000):
        if run["streams_status"] == "pending":
            continue
        if versions.get(run["id"]) == analysis.STATS_VERSION:
            continue
        activity = db.get_activity(run["id"])
        stats = analysis.compute(activity, db.get_streams(run["id"]))
        db.save_stats(run["id"], analysis.STATS_VERSION, stats)


sync_manager = SyncManager(on_complete=ensure_stats)
_pending_states = set()  # protects the login flow against forged callbacks


# ---------- pages ----------

@app.get("/")
def index():
    return send_from_directory(os.path.join(HERE, "static"), "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    if name == "config.js":
        return config_js()
    return send_from_directory(os.path.join(HERE, "static"), name)


# ---------- Strava login ----------

@app.get("/auth/login")
def auth_login():
    if not strava.is_configured():
        return redirect("/?error=config")
    state = secrets.token_urlsafe(16)
    _pending_states.add(state)
    return redirect(strava.authorize_url(REDIRECT_URI, state))


@app.get("/auth/callback")
def auth_callback():
    state = request.args.get("state", "")
    if state not in _pending_states:
        return redirect("/?error=state")
    _pending_states.discard(state)

    if request.args.get("error"):  # you pressed Cancel on Strava's page
        return redirect("/?error=denied")
    code = request.args.get("code")
    granted = request.args.get("scope", "")
    if not code:
        return redirect("/?error=denied")
    try:
        strava.exchange_code(code, granted)
    except strava.Offline:
        return redirect("/?error=offline")
    except strava.AuthError:
        return redirect("/?error=denied")
    return redirect("/?connected=1")


@app.post("/auth/disconnect")
def auth_disconnect():
    db.clear_auth()
    return jsonify(ok=True)


# ---------- data for the page ----------

@app.get("/api/status")
def api_status():
    auth = db.get_auth()
    scope = (auth or {}).get("scope") or ""
    return jsonify(
        configured=strava.is_configured(),
        connected=bool(auth and auth.get("refresh_token")),
        athlete_name=(auth or {}).get("athlete_name"),
        # If you untick "View data about your private activities" on Strava's page
        # we won't see private runs – tell you so rather than silently miss them.
        scope_ok=(not auth) or "activity:read_all" in scope,
        last_sync=db.get_meta("last_sync"),
        backfill_complete=db.get_meta("backfill_complete", False),
        # Once the website is set up, GitHub does the syncing (see cloud.py)
        cloud_mode=db.get_meta("cloud_mode", False),
        counts=db.counts(),
        sync=sync_manager.snapshot(),
    )


@app.post("/api/sync")
def api_sync():
    if not db.get_auth():
        return jsonify(started=False, reason="not_connected"), 400
    started = sync_manager.start()
    return jsonify(started=started)


def _pb_entry(run, seconds=None, extra=None):
    return {"run_id": run["id"], "name": run["name"], "date": run["start_date_local"],
            "seconds": seconds, **(extra or {})}


def personal_bests(runs):
    """Fastest 1 km / 5 km / 10 km anywhere inside any run, plus your longest run.

    A PB counts as "new" when it came from a run added by the most recent sync
    that brought in runs; we also show the previous best so you can see the gain.
    """
    new_ids = set(db.get_meta("last_sync_new_ids", []))
    with_stats = [r for r in runs if r["stats"] and not r.get("duplicate_of")]
    result = {}
    for key, metres in analysis.PB_DISTANCES.items():
        best = previous = None
        for r in with_stats:
            e = (r["stats"].get("best_efforts") or {}).get(key)
            if not e:
                continue
            entry = _pb_entry(r, e["seconds"], {"start_m": e["start_m"], "metres": metres})
            if best is None or e["seconds"] < best["seconds"]:
                best = entry
            if r["id"] not in new_ids and (previous is None or e["seconds"] < previous["seconds"]):
                previous = entry
        is_new = bool(best and best["run_id"] in new_ids)
        result[key] = {"metres": metres, "best": best, "is_new": is_new,
                       "previous": previous if is_new else None}

    longest = previous = None
    for r in with_stats:
        d = r["stats"]["distance_m"]
        entry = _pb_entry(r, extra={"distance_m": d})
        if longest is None or d > longest["distance_m"]:
            longest = entry
        if r["id"] not in new_ids and (previous is None or d > previous["distance_m"]):
            previous = entry
    is_new = bool(longest and longest["run_id"] in new_ids)
    result["longest"] = {"best": longest, "is_new": is_new,
                         "previous": previous if is_new else None}
    return result


def runs_payload():
    """Every run with its stats, plus PBs – shared by the Mac app and the website build."""
    ensure_stats()
    runs = db.list_runs_with_stats()
    dupes = analysis.find_duplicates(runs)
    for r in runs:
        r["duplicate_of"] = dupes.get(r["id"])
    return dict(runs=runs, pbs=personal_bests(runs), tags=db.all_tags(),
                new_ids=db.get_meta("last_sync_new_ids", []))


@app.get("/api/runs")
def api_runs():
    return jsonify(runs_payload())


@app.get("/api/runs/<int:activity_id>")
def api_run_detail(activity_id):
    activity = db.get_activity(activity_id)
    if not activity:
        return jsonify(error="not_found"), 404
    ensure_stats()
    run = next((r for r in db.list_runs_with_stats() if r["id"] == activity_id), None)
    detail = {}
    if activity["streams_status"] == "done":
        detail = analysis.detail(activity, db.get_streams(activity_id))
    return jsonify(run=run, detail=detail)


@app.get("/api/compare")
def api_compare():
    """?ids=1,2,3 – in the order you picked them (the first is the one others are measured against)."""
    try:
        ids = [int(x) for x in request.args.get("ids", "").split(",") if x][:4]
    except ValueError:
        return jsonify(error="bad_ids"), 400
    ensure_stats()
    by_id = {r["id"]: r for r in db.list_runs_with_stats()}
    runs, series = [], []
    for i in ids:
        activity = by_id.get(i)
        if not activity:
            continue
        s = None
        if activity["streams_status"] == "done":
            s = analysis.compare_series(db.get_activity(i), db.get_streams(i))
        runs.append(activity)
        series.append(s)
    return jsonify(runs=runs, series=series)


DEFAULT_MAX_HR = 185


@app.get("/api/settings")
def api_get_settings():
    return jsonify(max_hr=db.get_meta("max_hr", DEFAULT_MAX_HR))


@app.post("/api/settings")
def api_set_settings():
    body = request.get_json(silent=True) or {}
    try:
        max_hr = int(body.get("max_hr"))
    except (TypeError, ValueError):
        return jsonify(error="Please enter a number."), 400
    if not 120 <= max_hr <= 230:
        return jsonify(error="That doesn't look like a maximum heart rate – try a number between 120 and 230."), 400
    db.set_meta("max_hr", max_hr)
    return jsonify(max_hr=max_hr)


def config_js():
    # The published website gets its own config.js; the Mac version always talks to this server.
    return app.response_class('window.SITE_CONFIG = { mode: "server" };', mimetype="text/javascript")


@app.post("/api/runs/<int:activity_id>/tag")
def api_set_tag(activity_id):
    tag = ((request.get_json(silent=True) or {}).get("tag") or "").strip()[:40]
    db.set_tag(activity_id, tag)
    return jsonify(ok=True, tag=tag or None)


if __name__ == "__main__":
    db.init()
    ensure_stats()
    if not strava.is_configured():
        print("\n  ⚠  .env is missing STRAVA_CLIENT_ID or STRAVA_CLIENT_SECRET – see README.\n")
    print(f"\n  Running dashboard is ready →  http://localhost:{PORT}\n"
          "  (Press Ctrl+C in this window to stop it.)\n")
    # 127.0.0.1 = only this computer can open it. debug stays off so no secrets leak in errors.
    app.run(host="127.0.0.1", port=PORT, debug=False)
