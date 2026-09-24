// Overview: personal bests, the last 8 weeks, and a gentle check on the past 7 days.

const HARD_SHARE = 0.85;      // a run is "hard" if it averaged above 85% of your max heart rate
const LONG_JUMP = 0.10;       // flag a longest run more than 10% longer than recent weeks
let weekMetric = "distance";

async function renderOverview() {
  const [data, maxHr] = await Promise.all([DATA.runs(), DATA.maxHr()]);
  runsData = data;
  renderPBs(data.pbs, data.runs);

  const runs = data.runs.filter((r) => r.stats && !r.duplicate_of);
  renderWeeks(runs);
  renderEffort(runs, maxHr);
  renderSettings(runs, maxHr);
}

// ---------------------------------------------------------------- dates

function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
function mondayOf(d) {
  const day = startOfDay(d);
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7));
  return day;
}
const runDate = (r) => fmt.localDate(r.start_date_local);
const runKm = (r) => (r.stats.distance_m ?? r.summary_distance ?? 0);
const runSecs = (r) => (r.stats.moving_s ?? r.moving_time ?? 0);

// ---------------------------------------------------------------- weekly totals

function renderWeeks(runs) {
  const thisMonday = mondayOf(new Date());
  const weeks = [];
  for (let i = 7; i >= 0; i--) {
    const start = new Date(thisMonday); start.setDate(start.getDate() - 7 * i);
    const end = new Date(start); end.setDate(end.getDate() + 7);
    const inWeek = runs.filter((r) => runDate(r) >= start && runDate(r) < end);
    weeks.push({ start, current: i === 0, runs: inWeek.length,
      distance: inWeek.reduce((a, r) => a + runKm(r), 0), time: inWeek.reduce((a, r) => a + runSecs(r), 0) });
  }
  // Short labels on the bars (they're narrow on a phone); the unit is in the note above.
  const hm = (secs) => { const m = Math.round(secs / 60); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`; };
  const METRIC = {
    distance: { unit: "Kilometres each week.", value: (w) => w.distance / 1000, text: (w) => (w.distance / 1000).toFixed(1) },
    runs: { unit: "Number of runs each week.", value: (w) => w.runs, text: (w) => `${w.runs}` },
    time: { unit: "Time spent moving each week, in hours:minutes.", value: (w) => w.time / 3600, text: (w) => hm(w.time) },
  };

  const box = $("weeks");
  const draw = () => {
    const m = METRIC[weekMetric];
    const max = Math.max(...weeks.map(m.value), 0.0001);
    $("week-unit").textContent = m.unit;
    box.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.m === weekMetric)));
    const cols = $("week-cols");
    cols.replaceChildren(...weeks.map((w) => {
      const pct = (m.value(w) / max) * 100;
      const label = w.start.toLocaleDateString(undefined, { day: "numeric", month: "short" });
      const full = `Week of ${label}${w.current ? " (this week so far)" : ""}: ${(w.distance / 1000).toFixed(1)} km, ` +
        `${w.runs} run${w.runs === 1 ? "" : "s"}, ${fmt.duration(w.time)}`;
      return el("div", { class: `week-col${w.current ? " current" : ""}`, tabindex: "0", title: full, "aria-label": full },
        el("div", { class: "week-val" }, m.value(w) ? m.text(w) : ""),
        el("div", { class: "week-bar-wrap" }, el("div", { class: "week-bar", style: `height:${pct.toFixed(1)}%` })),
        el("div", { class: "week-label" }, w.current ? "This wk" : label));
    }));
    $("week-rows").replaceChildren(...[...weeks].reverse().map((w) => el("tr", {},
      el("td", {}, w.current ? "This week" : `w/c ${w.start.toLocaleDateString(undefined, { day: "numeric", month: "short" })}`),
      el("td", { class: "num" }, `${(w.distance / 1000).toFixed(1)} km`),
      el("td", { class: "num" }, String(w.runs)),
      el("td", { class: "num" }, w.runs ? fmt.duration(w.time) : "—"))));
  };
  box.querySelectorAll(".seg-btn").forEach((b) => { b.onclick = () => { weekMetric = b.dataset.m; draw(); }; });
  draw();
}

// ---------------------------------------------------------------- the past 7 days

function renderEffort(runs, maxHr) {
  const now = new Date();
  const weekAgo = new Date(now); weekAgo.setDate(weekAgo.getDate() - 7);
  const fourWeeksAgo = new Date(now); fourWeeksAgo.setDate(fourWeeksAgo.getDate() - 28);
  const recent = runs.filter((r) => runDate(r) >= weekAgo);
  const threshold = Math.round(maxHr * HARD_SHARE);
  const withHr = recent.filter((r) => r.stats.avg_hr);
  const hard = withHr.filter((r) => r.stats.avg_hr >= threshold);
  const items = [];

  // Hard runs
  const hardText = `${hard.length} hard run${hard.length === 1 ? "" : "s"}`;
  const explain = `Hard means averaging above 85% of your max heart rate (${threshold} bpm).`;
  if (hard.length > 2) {
    items.push(effortItem(true, hardText,
      `${explain} Most runs are best kept easy, at a pace you could chat at – try making the next one or two gentler.`));
  } else {
    items.push(effortItem(false, hardText, explain));
  }
  if (recent.length > withHr.length)
    items.push(el("p", { class: "muted small" }, `${recent.length - withHr.length} run(s) had no heart rate and aren't counted.`));

  // Long-run jump
  const longest = (list) => list.reduce((best, r) => (runKm(r) > (best ? runKm(best) : 0) ? r : best), null);
  const thisWeek = longest(recent);
  const before = longest(runs.filter((r) => runDate(r) >= fourWeeksAgo && runDate(r) < weekAgo));
  if (!thisWeek) {
    items.push(effortItem(false, "No runs yet", "Nothing to compare – enjoy the rest."));
  } else if (!before) {
    items.push(effortItem(false, `Longest run ${fmt.km(runKm(thisWeek))}`,
      "No runs in the 3 weeks before to compare with yet."));
  } else {
    const jump = runKm(thisWeek) / runKm(before) - 1;
    const pct = Math.round(jump * 100);
    const head = `Longest run ${fmt.km(runKm(thisWeek))}`;
    const base = `the 3 weeks before (${fmt.km(runKm(before))})`;
    if (jump > LONG_JUMP) {
      items.push(effortItem(true, head,
        `Up ${pct}% on ${base}. Growing your longest run 10–15% at a time gives your legs time to adapt` +
        `${jump > 0.15 ? " – maybe hold here for a week or two." : "."}`));
    } else {
      items.push(effortItem(false, head,
        `${pct >= 0 ? `Up ${pct}%` : `Down ${-pct}%`} on ${base} – a comfortable step.`));
    }
  }
  $("effort-items").replaceChildren(...items);
}

function effortItem(flag, head, text) {
  return el("div", { class: `effort ${flag ? "flag" : "ok"}` },
    el("span", { class: "note-icon", "aria-hidden": "true" }),
    el("div", {},
      el("div", { class: "effort-head" }, el("span", { class: "sr-only" }, flag ? "Worth knowing: " : "Looks fine: "), head),
      el("div", { class: "small" }, text)));
}

// ---------------------------------------------------------------- settings

function renderSettings(runs, maxHr) {
  const input = $("max-hr");
  input.value = maxHr;
  const highest = Math.max(0, ...runs.map((r) => r.stats.max_hr || 0));
  $("max-hr-hint").textContent = highest > maxHr
    ? `The highest heart rate recorded in your runs is ${highest} bpm – above your setting, so your real maximum is at least that. You may want to raise it.`
    : highest ? `The highest heart rate recorded in your runs so far is ${highest} bpm.` : "";
  $("max-hr-where").textContent = DATA.isStatic
    ? "On the website this is saved on this device only."
    : "";
  $("settings-form").onsubmit = async (e) => {
    e.preventDefault();
    const msg = $("settings-msg");
    try {
      const saved = await DATA.setMaxHr(input.value);
      msg.textContent = `Saved – max heart rate is now ${saved} bpm.`;
      msg.className = "small ok-text";
      renderEffort(runs, saved);
    } catch (err) {
      msg.textContent = err.message;
      msg.className = "small error-text";
    }
  };
}
