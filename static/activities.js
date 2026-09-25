// Other activities: walks, rides, gym sessions… everything on Strava that isn't a run.
// They're kept apart from runs, so they never change your running totals or bests.

async function renderActivities() {
  runsData = await DATA.runs();
  const acts = runsData.activities || [];
  const sel = $("sport-filter");
  const current = sel.value;
  const counts = {};
  for (const a of acts) counts[a.sport_type] = (counts[a.sport_type] || 0) + 1;
  const types = Object.keys(counts).sort((x, y) => counts[y] - counts[x]);
  sel.replaceChildren(el("option", { value: "" }, "everything"),
    ...types.map((t) => el("option", { value: t }, `${sport.plural(t)} (${counts[t]})`)));
  sel.value = types.includes(current) ? current : "";
  renderActivityList();
}

function renderActivityList() {
  const acts = runsData.activities || [];
  const filter = $("sport-filter").value;
  const shown = acts.filter((a) => !filter || a.sport_type === filter);
  $("activities-empty").hidden = acts.length > 0;
  $("activity-count").textContent = filter ? `(${shown.length} of ${acts.length})` : acts.length ? `(${acts.length})` : "";
  const since = new Date(startOfDay(new Date())); since.setDate(since.getDate() - 27);
  $("activity-totals").replaceChildren(...sportTotals(shown.filter((a) => runDate(a) >= since)));
  $("activity-totals").hidden = !shown.length;
  const newIds = new Set(runsData.new_ids);

  $("activity-rows").replaceChildren(...shown.map((a) => {
    const s = a.stats || {};
    const noHr = a.streams_status === "done" && !a.has_hr;
    const metres = s.distance_m ?? a.summary_distance;
    const row = el("tr", { class: `run-row${a.duplicate_of ? " is-dup" : ""}` },
      el("td", { class: "date", "data-label": "Date" },
        el("div", {}, fmt.day(a.start_date_local)),
        el("div", { class: "muted small" }, fmt.clock(a.start_date_local))),
      el("td", { class: "name-cell" },
        el("div", { class: "run-name" },
          el("span", { class: "sport-icon", title: sport.name(a.sport_type), "aria-hidden": "true" }, sport.icon(a.sport_type)),
          el("a", { href: `#/run/${a.id}` }, a.name), newIds.has(a.id) ? el("span", { class: "new-run" }, "new") : null),
        el("div", { class: "badges" }, el("span", { class: "badge sport-badge" }, sport.name(a.sport_type)), tagChip(a), ...runBadges(a))),
      el("td", { class: "num", "data-label": "Distance" }, pill("distance", metres ? fmt.km(metres) : "—")),
      el("td", { class: "num", "data-label": "Time" }, pill("time", fmt.duration(s.moving_s || a.moving_time))),
      el("td", { class: "num", "data-label": sport.isRide(a.sport_type) ? "Speed" : "Pace" },
        pill("pace", sport.speed(s.pace_s_per_km, a.sport_type))),
      el("td", { class: "num", "data-label": "Avg HR" }, pill("hr", noHr ? "—" : fmt.bpm(s.avg_hr ?? a.average_heartrate))),
      el("td", { class: "num", "data-label": "Max HR" }, pill("maxhr", noHr ? "—" : fmt.bpm(s.max_hr ?? a.max_heartrate))),
      el("td", { class: "num", "data-label": "Climb" }, pill("climb", metres ? fmt.metres(s.elev_gain_m) : "—")));
    row.addEventListener("click", (e) => {
      if (e.target.closest("button, input, a, select")) return;
      if (window.getSelection().toString()) return;
      location.hash = `#/run/${a.id}`;
    });
    return row;
  }));
}

// One tile per sport: how many, how far, how long. Used here and on the Overview.
function sportTotals(acts) {
  const by = {};
  for (const a of acts.filter((x) => !x.duplicate_of)) {
    const t = (by[a.sport_type] ||= { type: a.sport_type, n: 0, metres: 0, secs: 0 });
    t.n += 1;
    t.metres += (a.stats && a.stats.distance_m) ?? a.summary_distance ?? 0;
    t.secs += (a.stats && a.stats.moving_s) || a.moving_time || 0;
  }
  return Object.values(by).sort((x, y) => y.secs - x.secs).map((t) =>
    el("div", { class: "sport-total" },
      el("div", { class: "sport-total-head" }, el("span", { "aria-hidden": "true" }, sport.icon(t.type)), " ",
        sport.count(t.type, t.n)),
      el("div", { class: "small muted" }, [t.metres >= 100 ? fmt.km(t.metres, 1) : null, fmt.duration(t.secs)].filter(Boolean).join(" · "))));
}

document.addEventListener("DOMContentLoaded", () => {
  $("sport-filter").addEventListener("change", renderActivityList);
});
