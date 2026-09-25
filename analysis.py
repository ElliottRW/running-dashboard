"""Working out your stats from the recorded streams (not Strava's summary totals).

Distances come from the GPS track itself. Treadmill runs without GPS fall back to
the watch's distance recording, then to Strava's summary distance.
"""
import math

STATS_VERSION = 2           # bump when the maths changes, so stats get recalculated
RUN_TYPES = {"Run", "TrailRun"}  # everything else (walks, rides…) is an "other activity"
RIDE_TYPES = {"Ride", "VirtualRide", "EBikeRide", "EMountainBikeRide", "GravelRide", "MountainBikeRide",
              "Handcycle", "Velomobile"}
PB_DISTANCES = {"1k": 1000, "5k": 5000, "10k": 10000}
MOVING_SPEED = 0.6          # m/s – slower than this counts as stopped (traffic lights etc.)
GLITCH_SPEED = 12.0         # m/s – faster than Usain Bolt means a GPS jump, not you
RIDE_GLITCH_SPEED = 30.0    # m/s – bikes really can go 43 km/h+ downhill


def haversine(a, b):
    """Metres between two [lat, lng] points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371008.8 * 2 * math.asin(math.sqrt(h))


def smooth(values, half_window):
    """Centred moving average, skipping gaps (None)."""
    out = []
    n = len(values)
    for i in range(n):
        window = [v for v in values[max(0, i - half_window): min(n, i + half_window + 1)]
                  if v is not None]
        out.append(sum(window) / len(window) if window else None)
    return out


def is_run(activity):
    return (activity or {}).get("sport_type") in RUN_TYPES


def gps_distance(time, latlng, glitch_speed=GLITCH_SPEED):
    """Cumulative distance along the GPS track.

    No smoothing here: smoothing the position cuts corners and loses ~2%.
    Obvious GPS jumps (faster than glitch_speed) are skipped instead.
    """
    dist = [0.0]
    for i in range(1, len(latlng)):
        a, b = latlng[i - 1], latlng[i]
        step = 0.0
        if a and b:
            step = haversine(a, b)
            if step / max(time[i] - time[i - 1], 1) > glitch_speed:
                step = 0.0
        dist.append(dist[-1] + step)
    return dist


def cumulative_distance(streams, activity=None):
    """Returns (distance list or None, source) – see module docstring."""
    time = streams.get("time") or []
    if streams.get("latlng") and len(streams["latlng"]) == len(time):
        ride = (activity or {}).get("sport_type") in RIDE_TYPES
        return gps_distance(time, streams["latlng"], RIDE_GLITCH_SPEED if ride else GLITCH_SPEED), "gps"
    if streams.get("distance") and len(streams["distance"]) == len(time):
        return list(streams["distance"]), "watch"
    return None, "summary"


def best_effort(time, dist, target):
    """Fastest time to cover `target` metres, checking every start point in the run.

    Two pointers walk along the run; the finish time is interpolated between
    samples so the window is exactly `target` metres long.
    """
    n = len(dist)
    if n < 2 or dist[-1] < target:
        return None
    best = None
    j = 0
    for i in range(n):
        goal = dist[i] + target
        while j < n and dist[j] < goal:
            j += 1
        if j == n:
            break
        d0, d1 = dist[j - 1], dist[j]
        t0, t1 = time[j - 1], time[j]
        finish = t1 if d1 <= d0 else t0 + (t1 - t0) * (goal - d0) / (d1 - d0)
        secs = finish - time[i]
        if best is None or secs < best["seconds"]:
            best = {"seconds": round(secs, 1), "start_m": round(dist[i]), "start_s": time[i]}
    return best


def elevation_gain(altitude, threshold=1.0):
    """Total climb, ignoring wobbles smaller than `threshold` metres."""
    if not altitude:
        return None
    alt = [a for a in smooth(altitude, 3) if a is not None]  # gentle ~7-sample smoothing
    if not alt:
        return None
    gain, low = 0.0, alt[0]
    for a in alt[1:]:
        if a - low >= threshold:
            gain += a - low
            low = a
        elif a < low:
            low = a
    return round(gain, 1)


def compute(activity, streams):
    """All the per-run numbers the dashboard shows. `activity` is a DB row dict."""
    time = streams.get("time") or []
    dist, source = cumulative_distance(streams, activity)

    if dist:
        distance = dist[-1]
        moving = 0
        for i in range(1, len(time)):
            dt = time[i] - time[i - 1]
            if dt > 0 and (dist[i] - dist[i - 1]) / dt >= MOVING_SPEED:
                moving += dt
    else:
        distance = activity.get("summary_distance") or 0
        moving = activity.get("moving_time") or 0
    elapsed = (time[-1] - time[0]) if len(time) > 1 else activity.get("elapsed_time") or 0

    hr = streams.get("heartrate") or []
    avg_hr = max_hr = None
    if hr and len(hr) == len(time):
        # Weight each reading by how long it lasted, so gaps don't skew the average
        total = weighted = 0
        for i in range(1, len(time)):
            dt = time[i] - time[i - 1]
            if hr[i] and 0 < dt <= 30:
                weighted += hr[i] * dt
                total += dt
        avg_hr = round(weighted / total) if total else None
        max_hr = max(h for h in hr if h) if any(hr) else None

    efforts = {}
    if source == "gps" and is_run(activity):   # treadmill distances are estimates – not fair for PBs
        for key, metres in PB_DISTANCES.items():
            efforts[key] = best_effort(time, dist, metres)

    return {
        "version": STATS_VERSION,
        "distance_m": round(distance, 1),
        "distance_source": source,
        "moving_s": moving,
        "elapsed_s": elapsed,
        "pace_s_per_km": round(moving / (distance / 1000), 1) if distance > 50 and moving else None,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "elev_gain_m": elevation_gain(streams.get("altitude")),
        "best_efforts": efforts,
        "summary_distance_m": activity.get("summary_distance"),
    }


def find_duplicates(runs):
    """Runs that look like the same run uploaded twice (same start ±2 min, distance ±5%).

    Returns {duplicate_id: original_id}; the copy added to Strava later is the duplicate.
    """
    dupes = {}
    ordered = sorted(runs, key=lambda r: r["start_epoch"])
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if b["start_epoch"] - a["start_epoch"] > 120:
                break
            da = (a.get("stats") or {}).get("distance_m") or a.get("summary_distance") or 0
            db_ = (b.get("stats") or {}).get("distance_m") or b.get("summary_distance") or 0
            if da and db_ and abs(da - db_) / max(da, db_) <= 0.05:
                original, copy = sorted((a["id"], b["id"]))
                if original not in dupes:
                    dupes[copy] = original
    return dupes


# ======================================================================
# Single-run page: splits, chart series and the plain-English summary
# ======================================================================
from bisect import bisect_left

PACE_WINDOW_S = 30          # smooth pace over ~30 seconds
SLOWEST_PACE_SPEED = 1.0    # m/s – slower than ~16:40 /km is a stop/walk: gap in the pace line
SLOWEST_OTHER_SPEED = 0.5   # m/s – walks and hikes go slower, so only true stops leave a gap
CHART_POINTS = 800          # plenty for a smooth line, light enough for a phone


def interp(xs, ys, x):
    """Linear interpolation of y at x, where xs is sorted ascending."""
    if not xs:
        return None
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    j = bisect_left(xs, x)
    x0, x1, y0, y1 = xs[j - 1], xs[j], ys[j - 1], ys[j]
    if y0 is None or y1 is None:
        return y1 if y0 is None else y0
    return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def moving_time_series(time, dist):
    """Cumulative time spent moving at each sample (stops don't count)."""
    out = [0]
    for i in range(1, len(time)):
        dt = time[i] - time[i - 1]
        moving = dt > 0 and (dist[i] - dist[i - 1]) / dt >= MOVING_SPEED
        out.append(out[-1] + (dt if moving else 0))
    return out


def smoothed_speed(time, dist, half=PACE_WINDOW_S / 2):
    """Speed over a ~30 s window centred on each sample, in m/s."""
    n = len(time)
    out = [None] * n
    lo = hi = 0
    for i in range(n):
        while time[lo] < time[i] - half:
            lo += 1
        while hi < n - 1 and time[hi + 1] <= time[i] + half:
            hi += 1
        dt = time[hi] - time[lo]
        out[i] = (dist[hi] - dist[lo]) / dt if dt > 0 else None
    return out


def avg_hr_between(time, hr, t0, t1):
    total = weighted = 0
    i = max(bisect_left(time, t0), 1)
    while i < len(time) and time[i] <= t1:
        dt = time[i] - time[i - 1]
        if hr[i] and 0 < dt <= 30:
            weighted += hr[i] * dt
            total += dt
        i += 1
    return weighted / total if total else None


def splits(time, dist, mtime, alt, hr):
    """One row per km (plus the leftover bit at the end if it's 100 m or more)."""
    total = dist[-1]
    bounds = [k * 1000 for k in range(1, int(total // 1000) + 1)]
    if total - (bounds[-1] if bounds else 0) >= 100:
        bounds.append(total)
    rows, start = [], 0.0
    for end in bounds:
        metres = end - start
        secs = interp(dist, mtime, end) - interp(dist, mtime, start)
        t0, t1 = interp(dist, time, start), interp(dist, time, end)
        rows.append({
            "km": len(rows) + 1,
            "metres": round(metres),
            "seconds": round(secs, 1),
            "pace_s_per_km": round(secs / (metres / 1000), 1) if metres else None,
            "avg_hr": round(avg_hr_between(time, hr, t0, t1)) if hr and avg_hr_between(time, hr, t0, t1) else None,
            "elev_change_m": round(interp(dist, alt, end) - interp(dist, alt, start), 1) if alt else None,
        })
        start = end
    return rows


def _climb(alt, dist, a, b):
    """Metres climbed between distances a and b."""
    if not alt:
        return 0
    i, j = bisect_left(dist, a), bisect_left(dist, b)
    seg = [x for x in alt[i:j + 1] if x is not None]
    return sum(max(0, y - x) for x, y in zip(seg, seg[1:]))


def summarise(time, dist, mtime, alt, hr):
    """Short plain-English notes about pacing and heart rate."""
    notes = []
    total = dist[-1]
    moving_total = mtime[-1]
    if total < 2000 or moving_total < 600:
        return [{"topic": "pacing", "text": "This run is a bit short to say much about pacing."}]

    def pace(a, b):
        secs = interp(dist, mtime, b) - interp(dist, mtime, a)
        return secs / ((b - a) / 1000)

    # 1) The start
    if total >= 3000:
        first, rest = pace(0, 1000), pace(1000, total)
        diff = rest - first  # positive = first km was quicker
        if diff / rest >= 0.04:
            notes.append({"topic": "start", "flag": True, "text":
                f"You started fast: your first km was {fmt_pace(first)}, about {round(diff)} s per km quicker "
                f"than the rest of the run ({fmt_pace(rest)}). Starting a touch slower often makes the "
                f"later kilometres feel easier."})
        elif -diff / rest >= 0.04:
            notes.append({"topic": "start", "text":
                f"You eased in gently: your first km ({fmt_pace(first)}) was slower than the rest of the "
                f"run ({fmt_pace(rest)}). A relaxed start like this is a good habit."})
        else:
            notes.append({"topic": "start", "text":
                f"Your start was well judged: your first km ({fmt_pace(first)}) was close to your pace "
                f"for the rest of the run ({fmt_pace(rest)})."})

    # 2) Fade or build: first half vs second half
    mid = total / 2
    p1, p2 = pace(0, mid), pace(mid, total)
    change = (p2 - p1) / p1
    climb1, climb2 = _climb(alt, dist, 0, mid), _climb(alt, dist, mid, total)
    hill_note = ""
    if change > 0.03 and climb2 - climb1 >= 15:
        hill_note = f" Some of that is the hills: the second half had {round(climb2 - climb1)} m more climbing."
    elif change < -0.02 and climb1 - climb2 >= 15:
        hill_note = f" The first half did have {round(climb1 - climb2)} m more climbing, which helps explain it."
    if change > 0.03:
        notes.append({"topic": "halves", "flag": True, "text":
            f"Your pace faded: the second half ({fmt_pace(p2)}) was {round(p2 - p1)} s per km slower than "
            f"the first ({fmt_pace(p1)}).{hill_note}"})
    elif change < -0.02:
        notes.append({"topic": "halves", "text":
            f"Negative split – you ran the second half faster than the first ({fmt_pace(p2)} vs "
            f"{fmt_pace(p1)}). (A “negative split” just means finishing faster than you started, "
            f"which is usually a sign of good pacing.){hill_note}"})
    else:
        notes.append({"topic": "halves", "text":
            f"Nice and even: your first and second halves were within a few seconds per km of each "
            f"other ({fmt_pace(p1)} and {fmt_pace(p2)})."})

    # 3) Heart rate vs pace – cardiac drift
    if not hr:
        notes.append({"topic": "hr", "text": "No heart rate was recorded on this run, so there's nothing to say about effort."})
        return notes
    if moving_total < 20 * 60:
        notes.append({"topic": "hr", "text": "Heart rate drift is only worth checking on runs of 20 minutes or more."})
        return notes
    # Skip the first 15% as a warm-up while heart rate settles, then compare two halves.
    a = total * 0.15
    m = a + (total - a) / 2
    ta, tm, tb = interp(dist, time, a), interp(dist, time, m), interp(dist, time, total)
    h1, h2 = avg_hr_between(time, hr, ta, tm), avg_hr_between(time, hr, tm, tb)
    q1, q2 = pace(a, m), pace(m, total)
    if not h1 or not h2:
        return notes
    rise = h2 - h1
    pace_change = (q2 - q1) / q1          # positive = slower in the second part
    what = ("That's what's called cardiac drift: your heart working harder for the same speed, "
            "often from heat, hills, not drinking enough, or tiredness.")
    if rise >= 3 and pace_change > -0.02:
        held = "held steady" if pace_change <= 0.02 else "slowed"
        notes.append({"topic": "hr", "flag": True, "text":
            f"Your heart rate kept rising – up {round(rise)} bpm in the second part of the run "
            f"({round(h1)} → {round(h2)}) – while your pace {held}. {what}"})
    elif rise >= 3:
        notes.append({"topic": "hr", "text":
            f"Your heart rate rose {round(rise)} bpm in the second part ({round(h1)} → {round(h2)}), "
            f"but you also sped up, so that's expected rather than a sign of tiring."})
    elif pace_change > 0.03:
        notes.append({"topic": "hr", "text":
            f"Your heart rate stayed about the same ({round(h1)} → {round(h2)} bpm) even though your pace "
            f"slowed – you were working just as hard to go a little slower, which usually means you were "
            f"tiring (or the ground got hillier). Your heart rate didn't creep up, so this isn't cardiac "
            f"drift (heart rate rising while speed stays the same)."})
    else:
        notes.append({"topic": "hr", "text":
            f"Your heart rate held steady ({round(h1)} → {round(h2)} bpm) along with your pace – "
            f"no sign of cardiac drift (heart rate creeping up while speed stays the same)."})
    return notes


def fmt_pace(secs_per_km):
    s = round(secs_per_km)
    return f"{s // 60}:{s % 60:02d} /km"


def detail(activity, streams):
    """Everything the single-run page needs, already crunched."""
    time = streams.get("time") or []
    dist, source = cumulative_distance(streams, activity)
    run = is_run(activity)
    slowest = SLOWEST_PACE_SPEED if run else SLOWEST_OTHER_SPEED
    hr = streams.get("heartrate") if streams.get("heartrate") and len(streams["heartrate"]) == len(time) else None
    alt_raw = streams.get("altitude") if streams.get("altitude") and len(streams["altitude"]) == len(time) else None
    alt = smooth(alt_raw, 3) if alt_raw else None
    latlng = streams.get("latlng") if source == "gps" else None

    out = {"source": source, "has_hr": bool(hr), "has_gps": bool(latlng), "has_distance": bool(dist)}
    n = len(time)
    step = max(1, math.ceil(n / CHART_POINTS))
    idx = list(range(0, n, step))
    if n and idx[-1] != n - 1:
        idx.append(n - 1)

    if dist:
        mtime = moving_time_series(time, dist)
        speed = smoothed_speed(time, dist)
        out["series"] = {
            "x_km": [round(dist[i] / 1000, 4) for i in idx],
            "t": [time[i] for i in idx],
            "elevation": [round(alt[i], 1) if alt and alt[i] is not None else None for i in idx] if alt else None,
            "pace": [round(1000 / speed[i], 1) if speed[i] and speed[i] >= slowest else None for i in idx],
            "hr": [hr[i] for i in idx] if hr else None,
            "latlng": [latlng[i] for i in idx] if latlng else None,
        }
        out["splits"] = splits(time, dist, mtime, alt, hr)
        # The pacing notes are written for runs; other activities just get the numbers
        out["summary"] = summarise(time, dist, mtime, alt, hr) if run else []
    else:
        # No distance recording at all (e.g. some treadmill runs): heart rate by time only
        out["series"] = {"x_min": [round(time[i] / 60, 2) for i in idx],
                         "hr": [hr[i] for i in idx] if hr else None}
        out["splits"] = []
        out["summary"] = [{"topic": "pacing", "text":
                           "This run has no distance recording (common on a treadmill), so there are no "
                           "km splits or pacing notes – but your heart rate is shown below."}] if run else []

    if latlng:
        pts = [p for p in latlng if p]
        out["route"] = latlng if len(latlng) <= 4000 else latlng[::math.ceil(len(latlng) / 4000)]
        out["km_markers"] = []
        for k in range(1, int(dist[-1] // 1000) + 1):
            j = min(bisect_left(dist, k * 1000), n - 1)
            if latlng[j]:
                out["km_markers"].append({"km": k, "latlng": latlng[j]})
        out["start"], out["finish"] = pts[0], pts[-1]
    return out


# ======================================================================
# Compare page: runs lined up by distance (overlaps are found in compare.js)
# ======================================================================

GRID_M = 10          # compare runs every 10 metres


def compare_series(activity, streams):
    """One run resampled every 10 m, so different runs can be lined up by distance."""
    time = streams.get("time") or []
    dist, source = cumulative_distance(streams, activity)
    if not dist or dist[-1] < 100:
        return None
    n = len(time)
    hr = streams.get("heartrate") if streams.get("heartrate") and len(streams["heartrate"]) == n else None
    alt = smooth(streams["altitude"], 3) if streams.get("altitude") and len(streams["altitude"]) == n else None
    latlng = streams.get("latlng") if source == "gps" else None
    speed = smoothed_speed(time, dist)
    pace = [1000 / v if v and v >= SLOWEST_PACE_SPEED else None for v in speed]

    grid = [k * GRID_M for k in range(int(dist[-1] // GRID_M) + 1)]
    if grid[-1] < dist[-1]:
        grid.append(dist[-1])
    t0 = time[0]

    def at(values, d, digits):
        v = interp(dist, values, d)
        return round(v, digits) if v is not None else None

    out = {
        "distance_m": round(dist[-1], 1),
        "d": [round(d, 1) for d in grid],
        "t": [at(time, d, 1) - t0 for d in grid],
        "pace": [at(pace, d, 1) for d in grid],
        "hr": [at(hr, d, 0) for d in grid] if hr else None,
        "elevation": [at(alt, d, 1) for d in grid] if alt else None,
        "latlng": None,
    }
    if latlng:
        lats = [p[0] if p else None for p in latlng]
        lngs = [p[1] if p else None for p in latlng]
        out["latlng"] = [[at(lats, d, 6), at(lngs, d, 6)] for d in grid]
    return out
