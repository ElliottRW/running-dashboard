"""Background sync: fetch new runs and their detailed streams from Strava.

The first sync backfills your whole history. Everything is saved as it
arrives, so if Strava's rate limit is hit (or you close the app) the next
sync carries on from where it stopped instead of starting again.
"""
import threading
from datetime import datetime, timezone

import db
import strava

RUN_TYPES = {"Run", "TrailRun"}
STREAM_KEYS = "time,latlng,distance,altitude,heartrate,velocity_smooth,cadence"
PAGE_SIZE = 200  # the most Strava allows per page – fewer requests


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SyncManager:
    def __init__(self, on_complete=None):
        self.on_complete = on_complete  # e.g. recalculate stats after a sync
        self._new_ids = []
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.state = {"running": False, "phase": "idle", "message": "",
                      "done": 0, "total": 0, "paused_until": None,
                      "error": None, "error_kind": None, "new_runs": 0}

    # ----- public -----

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self.state.update(running=True, phase="starting", message="Starting sync…",
                              done=0, total=0, paused_until=None, error=None,
                              error_kind=None, new_runs=0)
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def snapshot(self):
        with self._lock:
            s = dict(self.state)
        s["usage"] = strava.usage.as_dict()
        return s

    # ----- internals -----

    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def _run(self):
        self._new_ids = []
        # During the very first import every run is "new", so don't celebrate PBs then
        history_was_complete = db.get_meta("backfill_complete", False)
        try:
            self._list_new_runs()
            self._backfill_history()
            self._fetch_streams()
            db.set_meta("last_sync", _now_iso())
            if history_was_complete and self._new_ids:
                db.set_meta("last_sync_new_ids", self._new_ids)
            if self.on_complete:
                self.on_complete()
            msg = "All up to date."
            if self.state["new_runs"]:
                msg = f"Found {self.state['new_runs']} new run(s). " + msg
            self._set(phase="done", message=msg, paused_until=None)
        except strava.NotConfigured:
            self._fail("config", "Your Strava Client ID and Secret aren't set yet. "
                                 "Add them to the .env file and restart the app.")
        except strava.AuthError:
            self._fail("auth", "Your Strava login has expired or was revoked. "
                               "Click “Connect to Strava” to log in again.")
        except strava.Offline:
            self._fail("offline", "Couldn't reach Strava – check your internet connection. "
                                  "Your saved runs still work offline.")
        except strava.RateLimited as e:
            # Only daily limits reach here; short pauses are handled in _call
            db.set_meta("last_sync", _now_iso())
            self._fail("daily_limit",
                       "Strava's daily limit reached. Your progress is saved – the next "
                       "sync after this time will carry on where it stopped.",
                       paused_until=e.resume_at.isoformat())
        except Exception as e:  # anything unexpected – show it rather than hide it
            self._fail("unknown", f"Something went wrong during sync: {e}")
        finally:
            self._set(running=False)

    def _fail(self, kind, message, paused_until=None):
        self._set(phase="error", error=message, error_kind=kind, message=message,
                  paused_until=paused_until)

    def _call(self, path, params=None):
        """Call Strava, waiting out 15-minute rate limits. Daily limits stop the sync."""
        while True:
            try:
                return strava.get(path, params)
            except strava.RateLimited as e:
                if e.daily:
                    raise
                previous = (self.state["phase"], self.state["message"])
                self._set(phase="paused", paused_until=e.resume_at.isoformat(),
                          message="Pausing to stay within Strava's limit of 100 requests "
                                  "per 15 minutes. It will carry on automatically.")
                while datetime.now(timezone.utc) < e.resume_at:
                    if self._stop.wait(5):
                        raise
                self._set(phase=previous[0], message=previous[1], paused_until=None)

    def _save_page(self, activities):
        found = 0
        for a in activities:
            if (a.get("sport_type") or a.get("type")) in RUN_TYPES:
                if db.upsert_activity(a):
                    found += 1
                    self._new_ids.append(a["id"])
        return found

    def _list_new_runs(self):
        """Anything newer than the newest run we already have."""
        newest = db.newest_start_epoch()
        if newest is None:
            return
        self._set(phase="listing", message="Checking Strava for new runs…")
        page = 1
        while True:
            _, acts = self._call("/athlete/activities",
                                 {"after": newest, "per_page": PAGE_SIZE, "page": page})
            acts = acts or []
            self._set(new_runs=self.state["new_runs"] + self._save_page(acts))
            if len(acts) < PAGE_SIZE:
                return
            page += 1

    def _backfill_history(self):
        """Walk backwards through your whole history, one page at a time.

        We remember the oldest activity seen so far ('backfill_before'), so an
        interrupted backfill resumes from there next time.
        """
        if db.get_meta("backfill_complete", False):
            return
        before = db.get_meta("backfill_before")
        total_found = db.counts()["runs"]
        while True:
            self._set(phase="listing",
                      message=f"Loading your run history from Strava… {total_found} runs found so far.")
            params = {"per_page": PAGE_SIZE}
            if before:
                params["before"] = before
            _, acts = self._call("/athlete/activities", params)
            acts = acts or []
            found = self._save_page(acts)
            total_found += found
            self._set(new_runs=self.state["new_runs"] + found)
            if acts:
                before = min(db.utc_epoch(a["start_date"]) for a in acts)
                db.set_meta("backfill_before", before)
            if len(acts) < PAGE_SIZE:
                db.set_meta("backfill_complete", True)
                return

    def _fetch_streams(self):
        ids = db.pending_stream_ids()
        total = len(ids)
        for i, activity_id in enumerate(ids):
            self._set(phase="streams", done=i, total=total,
                      message=f"Downloading run details {i + 1} of {total}…")
            status, data = self._call(f"/activities/{activity_id}/streams",
                                      {"keys": STREAM_KEYS, "key_by_type": "true"})
            if status == 404 or not data:
                db.mark_streams_missing(
                    activity_id,
                    "Strava has no detailed data for this run (it may be a manual entry, "
                    "deleted, or hidden from this app).")
                continue
            streams = {k: v.get("data") for k, v in data.items() if isinstance(v, dict)}
            if not streams.get("time"):
                db.mark_streams_missing(activity_id, "This run has no timed recording to analyse.")
                continue
            # Treadmill runs often have heart rate but no distance recording –
            # keep what's there; stats fall back to Strava's summary distance.
            note = None if streams.get("distance") else \
                "No distance recording (e.g. treadmill) – using Strava's distance instead."
            db.save_streams(activity_id, streams, note)
        self._set(done=total, total=total)
