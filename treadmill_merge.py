"""Add your watch's heart rate to a treadmill run, ready to upload to Strava.

The Technogym treadmill sends Strava your distance and speed but no heart rate, and the Fitbit
sends heart rate but no real distance. This joins them into one run (one lap per treadmill
session) and saves it in data/ twice: as a FIT file marked as a treadmill run, which is the one
to upload, and as a TCX file, which is easy to read. The README explains the steps.

    .venv/bin/python treadmill_merge.py WATCH.tcx [ACTIVITY_ID ...] [--offset SECONDS] [--github]

WATCH.tcx      the file exported from Fitbit
ACTIVITY_ID    the treadmill session(s) on Strava. Leave them out to use the ones the watch overlaps
--offset N     seconds to add to the watch's clock so it matches the treadmill's. Leave it out at
               first: you get a table to check the two clocks with, and nothing is written
--github       use the newest data GitHub has synced, without changing the data on your Mac
"""
import argparse
import atexit
import base64
import bisect
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import db

HERE = os.path.dirname(os.path.abspath(__file__))
NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
NS_X = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clock(dt, offset=0):
    return (dt + timedelta(seconds=offset)).strftime("%H:%M:%S")


# ---------------------------------------------------------------- reading

def read_watch(path):
    """The watch's name and its heart rate readings [(time, bpm)], from a TCX file."""
    raw = open(path, "rb").read()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        sys.exit(f"{path} doesn't look like a normal TCX file.")
    root = ET.fromstring(raw)
    hr = []
    for tp in root.iterfind(f".//{{{NS}}}Trackpoint"):
        t, v = tp.find(f"{{{NS}}}Time"), tp.find(f"{{{NS}}}HeartRateBpm/{{{NS}}}Value")
        if t is not None and v is not None:
            hr.append((parse_time(t.text), int(v.text)))
    if not hr:
        sys.exit(f"{path} has no heart rate readings.")
    name = root.find(f".//{{{NS}}}Creator/{{{NS}}}Name")
    return (name.text if name is not None and name.text else "watch"), sorted(hr)


def github_db():
    """Unlock the newest copy GitHub has synced into a temporary file; the Mac's data is untouched."""
    import cloud  # loads .env, which has SITE_PASSWORD
    subprocess.run(["git", "fetch", "--quiet"], cwd=HERE, check=True)
    locked = subprocess.run(["git", "show", "origin/main:state/running.db.locked"], cwd=HERE,
                            check=True, capture_output=True).stdout
    box = json.loads(locked)
    key = cloud.derive_key(cloud.password(), base64.b64decode(box["salt"]))
    fd, path = tempfile.mkstemp(suffix=".db")
    with os.fdopen(fd, "wb") as f:
        f.write(cloud.unlock(key, box))
    atexit.register(os.remove, path)
    return path


def find_sessions(con, ids, watch_name, hr):
    """The treadmill sessions to use: the ones asked for, or the ones the watch was recording during."""
    if ids:
        rows = [con.execute("SELECT id FROM activities WHERE id = ?", (i,)).fetchone() for i in ids]
        missing = [str(i) for i, r in zip(ids, rows) if r is None]
        if missing:
            sys.exit(f"Not found: {', '.join(missing)}. If it's a new run, add --github "
                     "(or run cloud.py pull first).")
    else:
        lo, hi = hr[0][0].timestamp() - 600, hr[-1][0].timestamp()
        rows = con.execute("SELECT id FROM activities WHERE start_epoch BETWEEN ? AND ? "
                           "AND streams_status = 'done' ORDER BY start_epoch", (lo, hi)).fetchall()
    sessions = []
    for (aid,) in rows:
        name, start, local, summary = con.execute(
            "SELECT name, start_date, start_date_local, summary_json FROM activities WHERE id = ?",
            (aid,)).fetchone()
        row = con.execute("SELECT data_json FROM streams WHERE activity_id = ?", (aid,)).fetchone()
        streams = json.loads(row[0]) if row else {}
        device = json.loads(summary or "{}").get("device_name")
        indoor = streams.get("distance") and streams.get("time") and not streams.get("latlng")
        if not ids and (not indoor or device == watch_name):
            continue                      # an outdoor run, or the watch's own upload
        if ids and not indoor:
            sys.exit(f"{aid} ({name}) has no treadmill distance to use.")
        start = parse_time(start)
        sessions.append({"id": aid, "name": name, "start": start, "device": device,
                         "local_offset": int((parse_time(local) - start).total_seconds()),
                         "streams": streams})
    if not sessions:
        sys.exit(f"No treadmill session found between {clock(hr[0][0])} and {clock(hr[-1][0])} UTC "
                 f"on {hr[0][0]:%d %b}. If it's a new run, add --github (or run cloud.py pull "
                 "first), or give its activity id (the number in its Strava web address).")
    return sorted(sessions, key=lambda s: s["start"])


# ---------------------------------------------------------------- joining

class HeartRate:
    """The watch's readings with its clock moved by `offset` seconds."""

    def __init__(self, readings, offset):
        shift = timedelta(seconds=offset)
        self.t = [t + shift for t, _ in readings]
        self.v = [v for _, v in readings]

    def at(self, t, within=5):
        """The nearest reading to time t, if there's one within a few seconds."""
        i = bisect.bisect_left(self.t, t)
        best = min((j for j in (i - 1, i) if 0 <= j < len(self.t)),
                   key=lambda j: abs(self.t[j] - t), default=None)
        if best is not None and abs((self.t[best] - t).total_seconds()) <= within:
            return self.v[best]
        return None

    def between(self, a, b):
        return self.v[bisect.bisect_left(self.t, a):bisect.bisect_right(self.t, b)]


def build_laps(sessions, hr):
    """One lap per treadmill session. Distance keeps counting up across laps."""
    laps, carried = [], 0.0
    for s in sessions:
        st = s["streams"]
        t, d = st["time"], st["distance"]
        v = st.get("velocity_smooth") or [None] * len(t)
        points = []
        for ti, di, vi in zip(t, d, v):
            when = s["start"] + timedelta(seconds=ti)
            points.append({"t": when,
                           "d": float(f"{carried + di - d[0]:.1f}"),
                           "v": float(f"{vi:.3f}") if vi is not None else None,
                           "hr": hr.at(when)})
        speeds = [x for x in v if x is not None]
        laps.append({"start": s["start"], "secs": t[-1] - t[0], "dist": d[-1] - d[0],
                     "max_speed": max(speeds) if speeds else None,
                     "hr": hr.between(s["start"] + timedelta(seconds=t[0]),
                                      s["start"] + timedelta(seconds=t[-1])),
                     "points": points})
        carried += d[-1] - d[0]
    return laps


# ---------------------------------------------------------------- checking the clocks

def belt_changes(sessions, min_change=0.25):
    """[(time, change in m/s)] wherever the belt sped up or slowed, including starting and stopping.
    Changes within 10 s of each other count as one."""
    changes = []
    for s in sessions:
        t, v = s["streams"]["time"], s["streams"].get("velocity_smooth")
        if not v:
            continue
        steps = [(t[i], v[i] - v[i - 1]) for i in range(1, len(t)) if abs(v[i] - v[i - 1]) >= min_change / 2]
        steps.append((t[-1], -v[-1]))                     # the belt stops when the session ends
        group = []
        for step in steps + [None]:
            if group and (step is None or step[0] - group[-1][0] > 10):
                net = sum(dv for _, dv in group)
                if abs(net) >= min_change:
                    changes.append((s["start"] + timedelta(seconds=group[0][0]), net))
                group = []
            if step:
                group.append(step)
    return changes


def print_check(sessions, hr, offset):
    cols = (-15, -10, -5, 0, 5, 10, 15, 20, 30)
    print(f"\nHeart rate around each belt change, with the watch moved {offset:+g} s "
          "(seconds from the change):")
    print(" " * 28 + "".join(f"{c:>+5}" if c else "    0" for c in cols))
    for when, dv in belt_changes(sessions):
        cells = [hr.at(when + timedelta(seconds=c), within=3) for c in cols]
        label = "speeds up" if dv > 0 else "slows    "
        print(f"  {clock(when)} {label} {dv:+5.2f}  " +
              "".join(f"{c:>5}" if c is not None else "    –" for c in cells))
    print("\nWhen the clocks line up, heart rate starts to rise or fall within about 5 s after each\n"
          "change. If it moves before the change, the watch is ahead: try a lower --offset.\n"
          "If it only moves 15 s or more after, try a higher one. Then run this again with\n"
          "--offset N to write the files.")


# ---------------------------------------------------------------- TCX

def write_tcx(path, laps, notes):
    out = []
    w = out.append
    w('<?xml version="1.0" encoding="UTF-8"?>')
    w(f'<TrainingCenterDatabase xmlns="{NS}" xmlns:ns3="{NS_X}">')
    w("  <Activities>")
    w('    <Activity Sport="Running">')
    w(f"      <Id>{iso(laps[0]['start'])}</Id>")
    for lap in laps:
        w(f'      <Lap StartTime="{iso(lap["start"])}">')
        w(f"        <TotalTimeSeconds>{lap['secs']:.1f}</TotalTimeSeconds>")
        w(f"        <DistanceMeters>{lap['dist']:.1f}</DistanceMeters>")
        if lap["max_speed"] is not None:
            w(f"        <MaximumSpeed>{lap['max_speed']:.3f}</MaximumSpeed>")
        w("        <Calories>0</Calories>")
        if lap["hr"]:
            w(f"        <AverageHeartRateBpm><Value>{round(sum(lap['hr']) / len(lap['hr']))}</Value></AverageHeartRateBpm>")
            w(f"        <MaximumHeartRateBpm><Value>{max(lap['hr'])}</Value></MaximumHeartRateBpm>")
        w("        <Intensity>Active</Intensity>")
        w("        <TriggerMethod>Manual</TriggerMethod>")
        w("        <Track>")
        for p in lap["points"]:
            w("          <Trackpoint>")
            w(f"            <Time>{iso(p['t'])}</Time>")
            w(f"            <DistanceMeters>{p['d']:.1f}</DistanceMeters>")
            if p["hr"] is not None:
                w(f"            <HeartRateBpm><Value>{p['hr']}</Value></HeartRateBpm>")
            if p["v"] is not None:
                w(f"            <Extensions><ns3:TPX><ns3:Speed>{p['v']:.3f}</ns3:Speed></ns3:TPX></Extensions>")
            w("          </Trackpoint>")
        w("        </Track>")
        w("      </Lap>")
    w(f"      <Notes>{escape(notes)}</Notes>")
    w("    </Activity>")
    w("  </Activities>")
    w("</TrainingCenterDatabase>")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


# ---------------------------------------------------------------- FIT
# A FIT file can say "treadmill" (sub_sport), the way Garmin watches mark their treadmill runs.
# The timer is paused between laps, so a gap between two sessions isn't counted as moving time.

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
ENUM, UINT8, UINT16, UINT32, UINT32Z = (0x00, "B"), (0x02, "B"), (0x84, "H"), (0x86, "I"), (0x8C, "I")
INVALID = {"B": 0xFF, "H": 0xFFFF, "I": 0xFFFFFFFF}
RUNNING, TREADMILL = 1, 1                       # sport / sub_sport
EV_TIMER, EV_SESSION, EV_LAP, EV_ACTIVITY = 0, 8, 9, 26
ET_START, ET_STOP, ET_STOP_ALL = 0, 1, 4
CRC_TABLE = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def fit_time(dt):
    return int((dt - FIT_EPOCH).total_seconds())


def crc(data, c=0):
    for byte in data:
        tmp = CRC_TABLE[c & 0xF]
        c = ((c >> 4) & 0x0FFF) ^ tmp ^ CRC_TABLE[byte & 0xF]
        tmp = CRC_TABLE[c & 0xF]
        c = ((c >> 4) & 0x0FFF) ^ tmp ^ CRC_TABLE[(byte >> 4) & 0xF]
    return c


class Fit:
    def __init__(self):
        self.body = bytearray()
        self.defs = {}   # global message number -> (local type, field layout)

    def msg(self, global_num, fields):
        """fields: [(field number, base type, value)]; None is written as 'invalid'."""
        layout = tuple((num, bt) for num, bt, _ in fields)
        if self.defs.get(global_num, (None, None))[1] != layout:
            local = self.defs[global_num][0] if global_num in self.defs else len(self.defs)
            assert local < 16
            self.defs[global_num] = (local, layout)
            self.body += struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
            for num, (code, fmt) in layout:
                self.body += struct.pack("<BBB", num, struct.calcsize(fmt), code)
        self.body += struct.pack("<B", self.defs[global_num][0])
        for num, (code, fmt), value in fields:
            self.body += struct.pack("<" + fmt, INVALID[fmt] if value is None else value)

    def event(self, t, ev, ev_type):
        self.msg(21, [(253, UINT32, fit_time(t)), (0, ENUM, ev), (1, ENUM, ev_type), (4, UINT8, 0)])

    def bytes(self):
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(self.body), b".FIT")
        header += struct.pack("<H", crc(header))
        data = header + bytes(self.body)
        return data + struct.pack("<H", crc(data))


def hr_stats(points):
    hrs = [p["hr"] for p in points if p["hr"] is not None]
    return (round(sum(hrs) / len(hrs)) if hrs else None), (max(hrs) if hrs else None)


def write_fit(path, laps, local_offset=0):
    """local_offset: seconds your clock was ahead of UTC (0 in winter in the UK, 3600 in summer)."""
    fit = Fit()
    start, end = laps[0]["points"][0]["t"], laps[-1]["points"][-1]["t"]
    fit.msg(0, [(0, ENUM, 4), (1, UINT16, 255), (2, UINT16, 0), (3, UINT32Z, 1), (4, UINT32, fit_time(start))])
    totals = []
    for i, lap in enumerate(laps):
        pts = lap["points"]
        fit.event(pts[0]["t"], EV_TIMER, ET_START)
        for p in pts:
            fit.msg(20, [(253, UINT32, fit_time(p["t"])),
                         (5, UINT32, round(p["d"] * 100)),
                         (6, UINT16, round(p["v"] * 1000) if p["v"] is not None else None),
                         (3, UINT8, p["hr"])])
        fit.event(pts[-1]["t"], EV_TIMER, ET_STOP_ALL)
        secs = (pts[-1]["t"] - pts[0]["t"]).total_seconds()
        dist = pts[-1]["d"] - pts[0]["d"]
        avg_hr, max_hr = hr_stats(pts)
        max_v = max((p["v"] for p in pts if p["v"] is not None), default=None)
        totals.append((secs, dist))
        fit.msg(19, [(254, UINT16, i), (253, UINT32, fit_time(pts[-1]["t"])), (0, ENUM, EV_LAP), (1, ENUM, ET_STOP),
                     (2, UINT32, fit_time(pts[0]["t"])), (7, UINT32, round(secs * 1000)), (8, UINT32, round(secs * 1000)),
                     (9, UINT32, round(dist * 100)), (13, UINT16, round(dist / secs * 1000)),
                     (14, UINT16, round(max_v * 1000) if max_v else None), (15, UINT8, avg_hr), (16, UINT8, max_hr),
                     (24, ENUM, 0), (25, ENUM, RUNNING), (39, ENUM, TREADMILL)])
    timer = sum(s for s, _ in totals)
    elapsed = (end - start).total_seconds()
    dist = sum(d for _, d in totals)
    all_pts = [p for lap in laps for p in lap["points"]]
    avg_hr, max_hr = hr_stats(all_pts)
    speeds = [p["v"] for p in all_pts if p["v"] is not None]
    max_v = max(speeds) if speeds else None
    fit.msg(18, [(254, UINT16, 0), (253, UINT32, fit_time(end)), (0, ENUM, EV_SESSION), (1, ENUM, ET_STOP),
                 (2, UINT32, fit_time(start)), (5, ENUM, RUNNING), (6, ENUM, TREADMILL),
                 (7, UINT32, round(elapsed * 1000)), (8, UINT32, round(timer * 1000)), (9, UINT32, round(dist * 100)),
                 (14, UINT16, round(dist / timer * 1000)), (15, UINT16, round(max_v * 1000) if max_v else None),
                 (16, UINT8, avg_hr), (17, UINT8, max_hr), (25, UINT16, 0), (26, UINT16, len(laps)), (28, ENUM, 0)])
    fit.msg(34, [(253, UINT32, fit_time(end)), (0, UINT32, round(timer * 1000)), (1, UINT16, 1), (2, ENUM, 0),
                 (3, ENUM, EV_ACTIVITY), (4, ENUM, ET_STOP), (5, UINT32, fit_time(end) + local_offset)])
    with open(path, "wb") as f:
        f.write(fit.bytes())
    return {"dist": dist, "timer": timer, "elapsed": elapsed, "avg_hr": avg_hr, "max_hr": max_hr}


# ---------------------------------------------------------------- main

def mmss(secs):
    secs = int(round(secs))
    return f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}" if secs >= 3600 else f"{secs // 60}:{secs % 60:02d}"


def main():
    ap = argparse.ArgumentParser(description="Add your watch's heart rate to a treadmill run.")
    ap.add_argument("watch", help="the TCX file exported from your watch")
    ap.add_argument("ids", nargs="*", type=int, help="the treadmill session(s) on Strava")
    ap.add_argument("--offset", type=float, help="seconds to add to the watch's clock")
    ap.add_argument("--github", action="store_true", help="use the newest data GitHub has synced")
    ap.add_argument("--db", default=db.DB_PATH, help="the database to read (default: data/running.db)")
    ap.add_argument("--notes", help="the description to give the run")
    args = ap.parse_args()

    watch_name, readings = read_watch(args.watch)
    con = sqlite3.connect(github_db() if args.github else args.db)
    sessions = find_sessions(con, args.ids, watch_name, readings)

    print("Treadmill session" + ("s:" if len(sessions) > 1 else ":"))
    for s in sessions:
        st = s["streams"]
        print(f"  {s['id']}  {s['name']}  {clock(s['start'], s['local_offset'])}  "
              f"{(st['distance'][-1] - st['distance'][0]) / 1000:.2f} km  {mmss(st['time'][-1] - st['time'][0])}")
    lo = sessions[0]["local_offset"]
    print(f"Heart rate: {watch_name}, {clock(readings[0][0], lo)}–{clock(readings[-1][0], lo)}")

    hr = HeartRate(readings, args.offset or 0)
    print_check(sessions, hr, args.offset or 0)
    if args.offset is None:
        return

    laps = build_laps(sessions, hr)
    device = sessions[0]["device"]
    what = f"{device} treadmill" if device else "treadmill"
    if args.notes:
        notes = args.notes
    elif len(sessions) == 1:
        notes = f"{what[0].upper()}{what[1:]} session with {watch_name} heart rate"
    else:
        notes = f"{len(sessions)} {what} sessions joined into one run, with {watch_name} heart rate"
    day = (sessions[0]["start"] + timedelta(seconds=lo)).strftime("%Y-%m-%d")
    base = os.path.join(db.DATA_DIR, f"{day}-treadmill-merged")
    os.makedirs(db.DATA_DIR, exist_ok=True)
    write_tcx(base + ".tcx", laps, notes)
    totals = write_fit(base + ".fit", laps, lo)
    points = [p for lap in laps for p in lap["points"]]
    with_hr = sum(1 for p in points if p["hr"] is not None)
    print(f"\nWrote {os.path.relpath(base, HERE)}.fit and .tcx: {totals['dist'] / 1000:.2f} km, "
          f"{mmss(totals['timer'])}, heart rate avg {totals['avg_hr']} max {totals['max_hr']} "
          f"({with_hr} of {len(points)} points have heart rate)")
    print("\nNext: upload the .fit file to Strava, delete the originals there so the run isn't counted\n"
          "twice, and once it has synced, give it the Treadmill tag on the Mac.")


if __name__ == "__main__":
    main()
