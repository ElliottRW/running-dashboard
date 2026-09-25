"""Background sync: fetch new activities (runs, walks, rides…) and their detailed streams from Strava.

The first sync backfills your whole history. Everything is saved as it
arrives, so if Strava's rate limit is hit (or you close the app) the next
sync carries on from where it stopped instead of starting again.
"""
import threading
import time
from datetime import datetime, timezone

import db
import strava

STREAM_KEYS = "time,latlng,distance,altitude,heartrate,velocity_smooth,cadence"
PAGE_SIZE = 200  # the most Strava allows per page – fewer requests


class OutOfTime(Exception):
    """The sync's time budget ran out (GitHub stops jobs after 2 hours)."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SyncManager:
    def __init__(self, on_complete=None):
        self.on_complete = on_complete  # e.g. recalculate stats after a sync
        self.time_budget_s = None       # stop (saving progress) rather than wait past this
        self._deadline = None
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
                              error_kind=None, new_runs=0, removed=0)
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
        self._deadline = time.monotonic() + self.time_budget_s if self.time_budget_s else None
        if not db.get_meta("all_activity_types", False):
            # Older versions only saved runs – go through your history once more to pick up
            # walks, rides and everything else. Runs already saved are simply kept.
            db.set_meta("backfill_complete", False)
            db.set_meta("backfill_before", None)
            db.set_meta("all_activity_types", True)
        # During the very first import every run is "new", so don't celebrate PBs then
        history_was_complete = db.get_meta("backfill_complete", False)
        try:
            self._list_new_runs()
            self._refresh_recent()
            self._backfill_history()
            self._fetch_streams()
            db.set_meta("last_sync", _now_iso())
            if history_was_complete and self._new_ids:
                db.set_meta("last_sync_new_ids", self._new_ids)
            if self.on_complete:
                self.on_complete()
            msg = "All up to date."
            if self.state.get("removed"):
                msg = f"Removed {self.state['removed']} activit{'y' if self.state['removed'] == 1 else 'ies'} deleted on Strava. " + msg
            if self.state["new_runs"]:
                msg = f"Found {self.state['new_runs']} new activit{'y' if self.state['new_runs'] == 1 else 'ies'}. " + msg
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
        except OutOfTime:
            db.set_meta("last_sync", _now_iso())
            if self.on_complete:
                self.on_complete()
            self._fail("time_limit", "Stopped for now to stay within the time limit. Your progress is "
                                     "saved – the next sync will carry on where it stopped.")
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
                if self._deadline and time.monotonic() + (e.resume_at - datetime.now(timezone.utc)).total_seconds() > self._deadline:
                    raise OutOfTime()
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
            if db.upsert_activity(a):
                found += 1
                self._new_ids.append(a["id"])
        return found

    def _list_new_runs(self):
        """Anything newer than the newest activity we already have."""
        newest = db.newest_start_epoch()
        if newest is None:
            return
        self._set(phase="listing", message="Checking Strava for new activities…")
        page = 1
        while True:
            _, acts = self._call("/athlete/activities",
                                 {"after": newest, "per_page": PAGE_SIZE, "page": page})
            acts = acts or []
            self._set(new_runs=self.state["new_runs"] + self._save_page(acts))
            if len(acts) < PAGE_SIZE:
                return
            page += 1

    def _refresh_recent(self):
        """Re-check your latest activities, so changes made on Strava come through:
        new names, private/public, sport changes, and activities you've deleted.

        Strava lists activities newest first, so one page covers everything back to the
        oldest activity on it – anything we have in that time span that isn't on the page
        has been deleted on Strava.
        """
        if not db.get_meta("backfill_complete", False):
            return   # the first import is still running and will fetch everything anyway
        self._set(phase="listing", message="Checking for changes made on Strava…")
        _, acts = self._call("/athlete/activities", {"per_page": PAGE_SIZE, "page": 1})
        acts = acts or []
        if not acts:
            return
        self._set(new_runs=self.state["new_runs"] + self._save_page(acts))
        # Without permission to see private activities, private ones would look "deleted" – so don't remove anything
        if "activity:read_all" not in ((db.get_auth() or {}).get("scope") or ""):
            return
        still_there = {a["id"] for a in acts}
        oldest = min(db.utc_epoch(a["start_date"]) for a in acts)
        for gone in db.ids_since(oldest) - still_there:
            db.delete_activity(gone)
            self._set(removed=self.state.get("removed", 0) + 1)

    def _backfill_history(self):
        """Walk backwards through your whole history, one page at a time.

        We remember the oldest activity seen so far ('backfill_before'), so an
        interrupted backfill resumes from there next time.
        """
        if db.get_meta("backfill_complete", False):
            return
        before = db.get_meta("backfill_before")
        total_found = db.counts()["activities"]
        while True:
            self._set(phase="listing",
                      message=f"Loading your history from Strava… {total_found} activities found so far.")
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
                      message=f"Downloading activity details {i + 1} of {total}…")
            status, data = self._call(f"/activities/{activity_id}/streams",
                                      {"keys": STREAM_KEYS, "key_by_type": "true"})
            if status == 404 or not data:
                db.mark_streams_missing(
                    activity_id,
                    "Strava has no detailed data for this activity (it may be a manual entry, "
                    "deleted, or hidden from this app).")
                continue
            streams = {k: v.get("data") for k, v in data.items() if isinstance(v, dict)}
            if not streams.get("time"):
                db.mark_streams_missing(activity_id, "This activity has no timed recording to analyse.")
                continue
            # Treadmill runs often have heart rate but no distance recording –
            # keep what's there; stats fall back to Strava's summary distance.
            note = None if streams.get("distance") else \
                "No distance recording (e.g. treadmill) – using Strava's distance instead."
            db.save_streams(activity_id, streams, note)
        self._set(done=total, total=total)
