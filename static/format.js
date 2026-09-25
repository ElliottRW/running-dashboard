// Small helpers for showing numbers the way runners read them.

const fmt = {
  // 1520 → "25:20", 3725 → "1:02:05"
  duration(secs) {
    if (secs == null) return "—";
    secs = Math.round(secs);
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    const ss = String(s).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
  },
  // seconds per km → "5:46 /km"
  pace(secsPerKm, unit = true) {
    if (!secsPerKm || !isFinite(secsPerKm)) return "—";
    const total = Math.round(secsPerKm);
    const p = `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
    return unit ? `${p} /km` : p;
  },
  km(metres, digits = 2) {
    return metres == null ? "—" : `${(metres / 1000).toFixed(digits)} km`;
  },
  bpm(v) { return v ? `${Math.round(v)}` : "—"; },
  metres(v) { return v == null ? "—" : `${Math.round(v)} m`; },

  // Strava's "local" start time looks like UTC ("…Z") but is really your clock time,
  // so read the numbers directly instead of letting the browser convert time zones.
  localDate(s) {
    const [d, t] = s.replace("Z", "").split("T");
    const [y, mo, da] = d.split("-").map(Number);
    const [h, mi] = t.split(":").map(Number);
    return new Date(y, mo - 1, da, h, mi);
  },
  day(s) {
    return fmt.localDate(s).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  },
  longDay(s) {
    return fmt.localDate(s).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  },
  clock(s) {
    return fmt.localDate(s).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  },
};

// ---------- sports other than running ----------

// Strava sport_type → how we show it. Anything not listed gets its name split into words.
const SPORTS = {
  Walk: ["🚶", "Walk"], Hike: ["🥾", "Hike"],
  Ride: ["🚴", "Ride"], VirtualRide: ["🚴", "Indoor ride"], EBikeRide: ["🚴", "E-bike ride"],
  EMountainBikeRide: ["🚵", "E-mountain bike ride"], GravelRide: ["🚴", "Gravel ride"], MountainBikeRide: ["🚵", "Mountain bike ride"],
  VirtualRun: ["🏃", "Virtual run"],
  Swim: ["🏊", "Swim"], Rowing: ["🚣", "Rowing"], VirtualRow: ["🚣", "Indoor rowing"], Kayaking: ["🛶", "Kayaking"],
  WeightTraining: ["🏋️", "Weights"], Workout: ["💪", "Workout"], Crossfit: ["💪", "CrossFit"], HighIntensityIntervalTraining: ["💪", "HIIT"],
  Yoga: ["🧘", "Yoga"], Pilates: ["🧘", "Pilates"], Elliptical: ["⚙️", "Elliptical"], StairStepper: ["🪜", "Stair stepper"],
  AlpineSki: ["⛷️", "Skiing"], NordicSki: ["⛷️", "Cross-country skiing"], Snowboard: ["🏂", "Snowboarding"],
  RockClimbing: ["🧗", "Climbing"], Tennis: ["🎾", "Tennis"], Soccer: ["⚽", "Football"], Golf: ["⛳", "Golf"],
};
const RIDES = new Set(["Ride", "VirtualRide", "EBikeRide", "EMountainBikeRide", "GravelRide", "MountainBikeRide", "Handcycle", "Velomobile"]);

const sport = {
  icon: (type) => (SPORTS[type] || ["🏅"])[0],
  name: (type) => (SPORTS[type] || [null, (type || "Activity").replace(/([a-z])([A-Z])/g, "$1 $2")])[1],
  // "Walks", "Mountain bike rides" – for filters
  plural(type, n = 2) {
    const name = sport.name(type);
    if (n === 1) return name;
    return sport.countable(type) ? `${name}s` : name;
  },
  countable: (type) => /(Walk|Hike|ride|Ride|Swim|run|Workout)$/.test(sport.name(type)),
  // "1 walk", "3 rides", "2 yoga sessions"
  count(type, n) {
    const name = sport.name(type).toLowerCase();
    const noun = sport.countable(type) ? name : `${name} session`;
    return `${n} ${noun}${n === 1 ? "" : "s"}`;
  },
  isRide: (type) => RIDES.has(type),
  isSwim: (type) => type === "Swim",
  // How fast, the way people usually say it: km/h on a bike, per 100 m in the pool, per km on foot
  speed(secsPerKm, type, unit = true) {
    if (!secsPerKm || !isFinite(secsPerKm)) return "—";
    if (sport.isRide(type)) return `${(3600 / secsPerKm).toFixed(1)}${unit ? " km/h" : ""}`;
    if (sport.isSwim(type)) return `${fmt.pace(secsPerKm / 10, false)}${unit ? " /100 m" : ""}`;
    return fmt.pace(secsPerKm, unit);
  },
  speedLabel: (type) => (sport.isRide(type) ? "Average speed" : "Average pace"),
};

// Build an element safely: text from Strava (run names, tags) is never treated as HTML.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
