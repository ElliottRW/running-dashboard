// The single-run page: headline numbers, plain-English summary, map, splits and profile chart.

let runMap = null;          // Leaflet map for the current run (removed when leaving the page)
let hoverMarker = null;     // dot on the map that follows the chart
let currentRun = null;

async function renderRunPage(id) {
  const page = $("page-run");
  page.replaceChildren(el("p", { class: "muted" }, "Loading run…"));
  if (runMap) { runMap.remove(); runMap = null; }

  const found = await DATA.run(id);
  if (!found) {
    page.replaceChildren(backLink(), el("div", { class: "card" }, "Couldn't find that run – it may have been deleted from Strava."));
    return;
  }
  const { run, detail } = found;
  currentRun = { run, detail };
  const s = run.stats || {};
  const noHr = run.streams_status === "done" && !run.has_hr;

  // ----- header -----
  const tiles = [
    ["Distance", fmt.km(s.distance_m ?? run.summary_distance)],
    ["Moving time", fmt.duration(s.moving_s ?? run.moving_time)],
    ["Average pace", fmt.pace(s.pace_s_per_km)],
    ["Avg heart rate", noHr ? "—" : `${fmt.bpm(s.avg_hr)} bpm`],
    ["Max heart rate", noHr ? "—" : `${fmt.bpm(s.max_hr)} bpm`],
    ["Climb", fmt.metres(s.elev_gain_m)],
  ];
  if (s.elapsed_s && s.moving_s && s.elapsed_s - s.moving_s >= 60)
    tiles.splice(2, 0, ["Total time", fmt.duration(s.elapsed_s)]);

  const header = el("section", { class: "card" },
    el("div", { class: "muted small" }, `${fmt.longDay(run.start_date_local)} · ${fmt.clock(run.start_date_local)}`),
    el("h1", { class: "run-title" }, run.name),
    el("div", { class: "badges" }, tagChip(run), ...runBadges(run)),
    el("div", { class: "tiles" }, ...tiles.map(([label, value]) =>
      el("div", { class: "tile" }, el("div", { class: "tile-label" }, label), el("div", { class: "tile-value" }, value)))),
    s.elapsed_s - s.moving_s >= 60
      ? el("p", { class: "muted small" }, "Moving time leaves out stops; total time includes them.") : null);

  // ----- summary -----
  const summary = (detail.summary || []).length ? el("section", { class: "card" },
    el("h2", {}, "How it went"),
    el("ul", { class: "notes" }, ...detail.summary.map((n) =>
      el("li", { class: n.flag ? "flag" : "" },
        n.flag ? el("span", { class: "note-label" }, "Worth knowing") : null, n.text)))) : null;

  // ----- map -----
  let mapCard = null;
  if (detail.has_gps) {
    mapCard = el("section", { class: "card" },
      el("h2", {}, "Route"),
      el("div", { id: "run-map", class: "map" }),
      el("p", { class: "muted small legend-line" },
        el("span", { class: "key-dot start" }, "S"), " start · ",
        el("span", { class: "key-dot finish" }, "F"), " finish · ",
        el("span", { class: "key-dot km" }, "1"), " each km"),
      detail.privacy_m ? el("p", { class: "muted small" },
        `For privacy, the route within ${detail.privacy_m} m of the start and finish isn't shown, so S and F mark where the visible route begins and ends.`) : null);
  } else if (detail.route_hidden) {
    mapCard = el("section", { class: "card muted" }, "This whole route is inside your privacy zone (close to where you usually start), so the map isn't shown on the website.");
  } else if (run.streams_status === "done") {
    mapCard = el("section", { class: "card muted" }, "No GPS on this run, so there's no map to show.");
  }

  // ----- splits -----
  const splitsCard = detail.splits && detail.splits.length ? splitsSection(detail.splits, s.pace_s_per_km) : null;

  // ----- profile chart -----
  const profileCard = detail.series ? profileSection(detail) : null;

  page.replaceChildren(backLink(run.id), header, summary, mapCard, splitsCard, profileCard,
    run.streams_status !== "done"
      ? el("section", { class: "card muted" }, run.streams_note || "The detailed recording for this run hasn't downloaded yet – try Sync now.")
      : null);

  if (detail.has_gps) drawMap(detail);
  if (profileCard) drawProfile();
}

function backLink(runId) {
  return el("div", { class: "page-links" },
    el("a", { href: "#/", class: "back" }, "← All runs"),
    runId ? el("a", { href: `#/compare/${runId}`, class: "back" }, "Compare with other runs →") : null);
}

// ---------------------------------------------------------------- map

function drawMap(d) {
  runMap = L.map("run-map", { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(runMap);

  const css = getComputedStyle(document.documentElement);
  const line = L.polyline(routeSegments(d.route), { color: css.getPropertyValue("--series-1").trim(), weight: 4, opacity: 0.9 }).addTo(runMap);
  runMap.fitBounds(line.getBounds(), { padding: [20, 20] });

  for (const m of d.km_markers)
    L.marker(m.latlng, { icon: L.divIcon({ className: "km-marker", html: String(m.km), iconSize: [20, 20] }),
      title: `${m.km} km`, keyboard: false }).addTo(runMap);

  const same = !d.privacy_m && L.latLng(d.start).distanceTo(L.latLng(d.finish)) < 60;
  if (same) {
    L.marker(d.start, { icon: L.divIcon({ className: "end-marker both", html: "S/F", iconSize: [30, 22] }), title: "Start and finish" }).addTo(runMap);
  } else {
    L.marker(d.start, { icon: L.divIcon({ className: "end-marker start", html: "S", iconSize: [22, 22] }), title: "Start" }).addTo(runMap);
    L.marker(d.finish, { icon: L.divIcon({ className: "end-marker finish", html: "F", iconSize: [22, 22] }), title: "Finish" }).addTo(runMap);
  }
  hoverMarker = L.circleMarker(d.start, { radius: 7, weight: 2, color: css.getPropertyValue("--surface").trim(),
    fillColor: css.getPropertyValue("--series-2").trim(), fillOpacity: 1, interactive: false });
}

// Split a route wherever points are missing (GPS gaps or privacy zones), so Leaflet
// doesn't draw a straight line across the gap.
function routeSegments(points) {
  const segs = [[]];
  for (const p of points) {
    if (p && p[0] != null) segs[segs.length - 1].push(p);
    else if (segs[segs.length - 1].length) segs.push([]);
  }
  return segs.filter((s) => s.length > 1);
}

function moveMapDot(latlng) {
  if (!runMap || !hoverMarker) return;
  if (!latlng || latlng[0] == null) { hoverMarker.remove(); return; }
  hoverMarker.setLatLng(latlng);
  if (!runMap.hasLayer(hoverMarker)) hoverMarker.addTo(runMap);
}

// ---------------------------------------------------------------- splits

function splitsSection(splits, avgPace) {
  // Longer bar = faster km. Bars start at zero so their lengths are honest.
  const fastest = Math.max(...splits.map((s) => 1000 / s.pace_s_per_km));
  const avgPct = avgPace ? (1000 / avgPace) / fastest * 100 : null;
  const rows = splits.map((s) => {
    const pct = (1000 / s.pace_s_per_km) / fastest * 100;
    const label = s.metres >= 995 ? `${s.km}` : `${(s.metres / 1000).toFixed(2)}`;
    const elev = s.elev_change_m == null ? "—" : `${s.elev_change_m > 0 ? "+" : s.elev_change_m < 0 ? "−" : "±"}${Math.abs(Math.round(s.elev_change_m))} m`;
    return el("div", { class: "split-row", role: "row" },
      el("div", { class: "split-km", role: "cell" }, label),
      el("div", { class: "split-bar-wrap", role: "cell", "aria-hidden": "true" },
        el("div", { class: "split-bar", style: `width:${pct.toFixed(1)}%` }),
        avgPct ? el("div", { class: "split-avg", style: `left:${avgPct.toFixed(1)}%` }) : null),
      el("div", { class: "split-pace num", role: "cell" }, fmt.pace(s.pace_s_per_km, false)),
      el("div", { class: "split-hr num", role: "cell" }, s.avg_hr ? `${s.avg_hr}` : "—"),
      el("div", { class: "split-elev num", role: "cell" }, elev));
  });
  return el("section", { class: "card" },
    el("h2", {}, "Km splits"),
    el("p", { class: "muted small" },
      "A “split” is the time for each kilometre. Longer bar = faster km; the thin line marks your average pace. ",
      "Heart rate is the average for that km, and height is how much you went up (+) or down (−)."),
    el("div", { class: "splits", role: "table", "aria-label": "Kilometre splits" },
      el("div", { class: "split-row split-head", role: "row" },
        el("div", { role: "columnheader" }, "Km"), el("div", { role: "columnheader" }, ""),
        el("div", { class: "num", role: "columnheader" }, "Pace"),
        el("div", { class: "num", role: "columnheader" }, "HR"),
        el("div", { class: "num", role: "columnheader" }, "Height")),
      ...rows));
}

// ---------------------------------------------------------------- profile chart

const METRICS = {
  elevation: { label: "Elevation", unit: "m", key: "elevation", format: (v) => `${Math.round(v)} m` },
  pace: { label: "Pace", key: "pace", invert: true, format: (v) => fmt.pace(v), tick: (v) => fmt.pace(v, false) },
  hr: { label: "Heart rate", key: "hr", format: (v) => `${Math.round(v)} bpm` },
};
let profileMetric = "elevation";

function profileSection(d) {
  const available = Object.entries(METRICS).filter(([, m]) => d.series[m.key] && d.series[m.key].some((v) => v != null));
  if (!available.length) return null;
  if (!available.some(([k]) => k === profileMetric)) profileMetric = available[0][0];
  const buttons = available.map(([k, m]) =>
    el("button", { type: "button", class: "seg-btn", "aria-pressed": String(k === profileMetric),
      onclick: () => { profileMetric = k; drawProfile(); } }, m.label));
  return el("section", { class: "card" },
    el("div", { class: "list-head" },
      el("h2", {}, d.series.x_km ? "Profile by distance" : "Heart rate over time"),
      el("div", { class: "segmented", role: "group", "aria-label": "Show" }, ...buttons)),
    el("p", { class: "muted small", id: "profile-note" }),
    el("div", { id: "profile-chart", class: "chart", tabindex: "0",
      "aria-label": "Run profile chart. Use left and right arrow keys to move along the run." }));
}

function drawProfile() {
  const d = currentRun.detail;
  const m = METRICS[profileMetric];
  document.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.textContent === m.label)));
  $("profile-note").textContent = {
    elevation: "Height above sea level, lightly smoothed so GPS wobble doesn't look like hills.",
    pace: "Pace smoothed over about 30 seconds so GPS jitter doesn't look like surges. Faster is higher up. Gaps are where you stopped.",
    hr: "Heart rate in beats per minute.",
  }[profileMetric];

  const xs = d.series.x_km || d.series.x_min;
  lineChart($("profile-chart"), {
    xs, ys: d.series[m.key], invert: m.invert, area: profileMetric === "elevation",
    topLabel: m.invert ? "faster ↑" : null,
    xFormat: d.series.x_km ? (v) => `${+v.toFixed(1)} km` : (v) => `${Math.round(v)} min`,
    yFormat: m.tick || ((v) => `${Math.round(v)}`),
    tooltip: (i) => {
      const rows = [[d.series.x_km ? `${xs[i].toFixed(2)} km` : `${Math.round(xs[i])} min`,
                     d.series.t ? `${fmt.duration(d.series.t[i])} in` : ""]];
      for (const [k, mm] of Object.entries(METRICS)) {
        const v = d.series[mm.key] && d.series[mm.key][i];
        if (d.series[mm.key]) rows.push([v == null ? (k === "pace" ? "stopped" : "—") : mm.format(v), mm.label]);
      }
      return rows;
    },
    onHover: (i) => moveMapDot(i == null || !d.series.latlng ? null : d.series.latlng[i]),
  });
}

// A small, dependency-free line chart with a crosshair.
// One line:   o.ys = [...]
// Several:    o.series = [{ ys, color, dash }]   (used by the Compare page)
// Returns { show(i) } so something else (e.g. a slider) can move the crosshair.
function lineChart(container, o) {
  const series = o.series || [{ ys: o.ys }];
  const W = Math.max(container.clientWidth, 280), H = o.height || 230;
  const pad = { l: o.padLeft || 46, r: 12, t: 12, b: 28 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const vals = series.flatMap((s) => s.ys || []).filter((v) => v != null).sort((a, b) => a - b);
  if (!vals.length) { container.replaceChildren(el("p", { class: "muted small" }, "No data to show.")); return { show() {} }; }
  // Pace: ignore the most extreme 2% so a single walk break doesn't squash the line
  let lo = o.invert ? vals[Math.floor(vals.length * 0.02)] : vals[0];
  let hi = o.invert ? vals[Math.ceil(vals.length * 0.98) - 1] : vals[vals.length - 1];
  if (o.includeZero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  const ticks = niceTicks(lo, hi, 4, o.tickSteps || (o.invert ? [5, 10, 15, 30, 60, 120, 300] : null));
  lo = ticks[0]; hi = ticks[ticks.length - 1];
  const x0 = o.xs[0], x1 = o.xs[o.xs.length - 1];
  const sx = (x) => pad.l + ((x - x0) / (x1 - x0 || 1)) * iw;
  const sy = (y) => {
    const f = (Math.min(Math.max(y, lo), hi) - lo) / (hi - lo || 1);
    return pad.t + (o.invert ? f : 1 - f) * ih;
  };
  const pathFor = (ys) => {
    let d = "", pen = false;
    o.xs.forEach((x, i) => {
      const y = ys[i];
      if (y == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const xTicks = niceTicks(x0, x1, W < 500 ? 4 : 7).filter((t) => t >= x0 && t <= x1);
  const styleFor = (s) => (s.color ? `stroke:${s.color};` : "") + (s.dash ? `stroke-dasharray:${s.dash};` : "");

  const svgMarkup = `
    <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">
      ${ticks.map((t) => `<line class="grid${t === 0 && o.includeZero ? " zero" : ""}" x1="${pad.l}" x2="${W - pad.r}" y1="${sy(t)}" y2="${sy(t)}"/>
        <text class="axis" x="${pad.l - 6}" y="${sy(t) + 4}" text-anchor="end">${o.yFormat(t)}</text>`).join("")}
      ${xTicks.map((t) => `<text class="axis" x="${sx(t)}" y="${H - 8}" text-anchor="middle">${o.xFormat(t)}</text>`).join("")}
      ${o.topLabel ? `<text class="axis" x="${pad.l + 4}" y="${pad.t + 10}">${o.topLabel}</text>` : ""}
      ${o.bottomLabel ? `<text class="axis" x="${pad.l + 4}" y="${pad.t + ih - 4}">${o.bottomLabel}</text>` : ""}
      ${o.area && series.length === 1 ? `<path class="area" d="${pathFor(series[0].ys)}L${sx(x1).toFixed(1)},${pad.t + ih}L${sx(x0).toFixed(1)},${pad.t + ih}Z"/>` : ""}
      ${series.map((s) => `<path class="line" style="${styleFor(s)}" d="${pathFor(s.ys || [])}"/>`).join("")}
      <line class="crosshair" y1="${pad.t}" y2="${pad.t + ih}" visibility="hidden"/>
      ${series.map((s) => `<circle class="hover-dot" r="5" style="${s.color ? `fill:${s.color}` : ""}" visibility="hidden"/>`).join("")}
      <rect class="hit" x="${pad.l}" y="0" width="${iw}" height="${H}" fill="transparent"/>
    </svg>`;
  container.innerHTML = svgMarkup;   // only numbers and our own fixed labels go into this markup
  const tip = el("div", { class: "tooltip", hidden: true });
  container.append(tip);

  const svgEl = container.querySelector("svg");
  const cross = svgEl.querySelector(".crosshair");
  const dots = [...svgEl.querySelectorAll(".hover-dot")];
  let current = null;

  function show(i, fromOutside) {
    current = i;
    if (i == null) {
      cross.setAttribute("visibility", "hidden");
      dots.forEach((d) => d.setAttribute("visibility", "hidden"));
      tip.hidden = true;
      if (!fromOutside && o.onHover) o.onHover(null);
      return;
    }
    const cx = sx(o.xs[i]);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    series.forEach((s, k) => {
      const y = s.ys && s.ys[i];
      if (y != null) { dots[k].setAttribute("cx", cx); dots[k].setAttribute("cy", sy(y)); dots[k].setAttribute("visibility", "visible"); }
      else dots[k].setAttribute("visibility", "hidden");
    });
    if (o.tooltip) {
      tip.replaceChildren(...o.tooltip(i).map(([v, label], k) =>
        el("div", { class: k === 0 ? "tip-head" : "tip-row" }, el("strong", {}, v), label ? ` ${label}` : "")));
      tip.hidden = false;
      const left = cx + 12 + tip.offsetWidth > W ? cx - 12 - tip.offsetWidth : cx + 12;
      tip.style.left = `${left}px`;
    }
    if (!fromOutside && o.onHover) o.onHover(i);
  }
  const nearest = (evt) => {
    const r = svgEl.getBoundingClientRect();
    const x = x0 + ((evt.clientX - r.left) * (W / r.width) - pad.l) / iw * (x1 - x0);
    let a = 0, b = o.xs.length - 1;
    while (b - a > 1) { const mid = (a + b) >> 1; if (o.xs[mid] < x) a = mid; else b = mid; }
    return Math.abs(o.xs[a] - x) <= Math.abs(o.xs[b] - x) ? a : b;
  };
  svgEl.addEventListener("pointermove", (e) => show(nearest(e)));
  svgEl.addEventListener("pointerdown", (e) => show(nearest(e)));
  if (!o.keepOnLeave) svgEl.addEventListener("pointerleave", () => show(null));
  container.onkeydown = (e) => {
    const stepN = Math.max(1, Math.round(o.xs.length / 100));
    if (e.key === "ArrowRight") { show(Math.min((current ?? -1) + stepN, o.xs.length - 1)); e.preventDefault(); }
    if (e.key === "ArrowLeft") { show(Math.max((current ?? stepN) - stepN, 0)); e.preventDefault(); }
    if (e.key === "Escape" && !o.keepOnLeave) show(null);
  };
  if (!o.keepOnLeave) container.onblur = () => show(null);
  return { show };
}

// Round axis values. Pace passes its own steps so ticks land on e.g. 5:30, 6:00.
function niceTicks(lo, hi, count, steps) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = steps ? (steps.find((s) => s >= raw) || steps[steps.length - 1])
    : [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.999; v += step) out.push(+v.toFixed(6));
  if (out[out.length - 1] < hi) out.push(out[out.length - 1] + step);
  return out;
}

window.addEventListener("resize", () => {
  if (currentRun && !$("page-run").hidden && $("profile-chart")) drawProfile();
});
