// Compare page: 2–4 runs side by side, lined up by distance.

const MAX_COMPARE = 4;
// Each run gets a colour AND a number AND a line style, so colour is never the only clue.
const RUN_STYLES = [
  { color: "var(--c1)", dash: null, leafletDash: null, ink: "#fff" },
  { color: "var(--c2)", dash: "8 5", leafletDash: "10 7", ink: "#fff" },
  { color: "var(--c3)", dash: "2 4", leafletDash: "2 8", ink: "#0b0b0b" },
  { color: "var(--c4)", dash: "10 4 2 4", leafletDash: "12 6 2 6", ink: "#0b0b0b" },
];
let compareMap = null, compareDots = [], compareChart = null, compareData = null;
let compareMetric = "gap";

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function runColor(i) { return cssVar(`--c${i + 1}`); }

async function renderComparePage(ids) {
  const page = $("page-compare");
  if (compareMap) { compareMap.remove(); compareMap = null; }
  if (!runsData) runsData = await DATA.runs();
  page.replaceChildren(pickerCard(ids));
  if (ids.length < 2) {
    page.append(el("section", { class: "card muted" },
      ids.length ? "Now add at least one more run to compare with." : "Pick two to four runs above to compare them."));
    return;
  }
  page.append(el("p", { class: "muted" }, "Lining up your runs…"));
  compareData = await DATA.compare(ids);
  compareData.overlaps = findOverlaps(compareData.series);
  page.lastChild.remove();
  buildCompare(page, compareData);
}

// ---------------------------------------------------------------- picking runs

function pickerCard(ids) {
  const byId = Object.fromEntries(runsData.runs.map((r) => [r.id, r]));
  const chips = ids.map((id, i) => {
    const r = byId[id];
    if (!r) return null;
    const rest = ids.filter((x) => x !== id);
    return el("div", { class: "pick-chip" },
      lineKey(i), runBadge(i),
      el("div", { class: "pick-text" },
        el("div", { class: "pick-name" }, r.name),
        el("div", { class: "muted small" }, `${fmt.day(r.start_date_local)} · ${fmt.km(r.stats?.distance_m ?? r.summary_distance)}`,
          i === 0 ? " · the others are measured against this one" : "")),
      i > 0 ? el("button", { type: "button", class: "btn link small", title: "Measure the others against this run",
        onclick: () => { location.hash = `#/compare/${[id, ...rest].join(",")}`; } }, "Make run 1") : null,
      el("button", { type: "button", class: "icon-btn", "aria-label": `Remove ${r.name}`,
        onclick: () => { location.hash = `#/compare/${rest.join(",")}`; } }, "×"));
  });

  const usable = runsData.runs.filter((r) => r.streams_status === "done" && r.stats && r.stats.distance_source !== "summary" && !ids.includes(r.id));
  const select = el("select", { "aria-label": "Add a run to compare", disabled: ids.length >= MAX_COMPARE },
    el("option", { value: "" }, ids.length >= MAX_COMPARE ? "Four runs is the most" : "+ Add a run…"),
    ...usable.map((r) => el("option", { value: r.id },
      `${fmt.day(r.start_date_local)} – ${r.name} – ${fmt.km(r.stats.distance_m)}${r.duplicate_of ? " (duplicate)" : ""}${r.tag ? ` [${r.tag}]` : ""}`)));
  select.addEventListener("change", () => {
    if (select.value) location.hash = `#/compare/${[...ids, select.value].join(",")}`;
  });

  return el("section", { class: "card" },
    el("h2", {}, "Head to head"),
    el("p", { class: "muted small" }, "Pick 2–4 runs. They're lined up by distance; the first one is the reference."),
    el("div", { class: "picks" }, ...chips),
    el("label", { class: "add-run" }, select));
}

function runBadge(i) {
  return el("span", { class: "run-badge", style: `background:${RUN_STYLES[i].color};color:${RUN_STYLES[i].ink}` }, String(i + 1));
}

function lineKey(i) {
  const s = RUN_STYLES[i];
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "28"); svg.setAttribute("height", "10"); svg.setAttribute("aria-hidden", "true");
  svg.classList.add("line-key");
  const ln = document.createElementNS("http://www.w3.org/2000/svg", "line");
  ln.setAttribute("x1", "1"); ln.setAttribute("x2", "27"); ln.setAttribute("y1", "5"); ln.setAttribute("y2", "5");
  ln.setAttribute("style", `stroke:${s.color};stroke-width:3;stroke-linecap:round;${s.dash ? `stroke-dasharray:${s.dash}` : ""}`);
  svg.append(ln);
  return svg;
}

function legend(runs) {
  return el("div", { class: "legend" }, ...runs.map((r, i) =>
    el("span", { class: "legend-item" }, lineKey(i), runBadge(i), el("span", {}, `${fmt.day(r.start_date_local)}`))));
}

// ---------------------------------------------------------------- the comparison

function buildCompare(page, data) {
  const { runs, series, overlaps } = data;
  const maxM = Math.max(...series.map((s) => (s ? s.distance_m : 0)));
  const minM = Math.min(...series.filter(Boolean).map((s) => s.distance_m));
  const K = Math.floor(maxM / 10);
  const xs = Array.from({ length: K + 1 }, (_, k) => (k * 10) / 1000);
  const valueAt = (s, key, k) => (s && s[key] && k < s[key].length && s.d[k] <= s.distance_m ? s[key][k] : null);
  // Height is shown relative to each run's own start, so every run begins at 0 m
  const startHeight = series.map((s) => (s && s.elevation ? s.elevation.find((v) => v != null) : null));
  const heightAt = (i, k) => {
    const v = valueAt(series[i], "elevation", k);
    return v == null || startHeight[i] == null ? null : v - startHeight[i];
  };
  const signedM = (v) => (Math.round(v) === 0 ? "0 m" : `${v > 0 ? "+" : "−"}${Math.abs(Math.round(v))} m`);

  // ----- notes: who was ahead, shared roads, opposite directions -----
  const notes = [];
  const common = Math.floor(minM / 10);
  const ref = series[0];
  runs.forEach((r, i) => {
    if (i === 0 || !series[i] || !ref) return;
    const gap = valueAt(series[i], "t", common) - valueAt(ref, "t", common);
    const over = fmt.km(common * 10, 1);
    notes.push(Math.abs(gap) < 2
      ? `Over the first ${over}, runs 1 and ${i + 1} were level.`
      : `Over the first ${over}, run ${i + 1} was ${fmt.duration(Math.abs(gap))} ${gap < 0 ? "faster" : "slower"} than run 1.`);
  });
  for (const p of overlaps.pairs) {
    const total = p.same_m + p.opposite_m;
    let text = `Runs ${p.a + 1} and ${p.b + 1} share about ${fmt.km(total, 1)} of the same roads`;
    if (p.opposite_m >= 100 && p.same_m >= 100)
      text += ` – ${fmt.km(p.same_m, 1)} in the same direction and ${fmt.km(p.opposite_m, 1)} in opposite directions (you ran that part the other way round).`;
    else if (p.opposite_m >= 100)
      text += `, but in opposite directions – you ran them the other way round.`;
    else text += `, in the same direction.`;
    notes.push(text);
  }
  const gpsCount = series.filter((s) => s && s.latlng).length;
  if (gpsCount >= 2 && !overlaps.pairs.length) notes.push("These routes don't share any stretches of road (they may cross, but don't run alongside each other).");

  // ----- map -----
  const hasMap = gpsCount > 0;
  const mapCard = hasMap ? el("section", { class: "card" },
    el("h2", {}, "The routes"),
    legend(runs),
    el("div", { id: "compare-map", class: "map" }),
    el("p", { class: "muted small legend-line" },
      el("span", { class: "band-key same" }), " shared road, same direction · ",
      el("span", { class: "band-key opposite" }), " shared road, opposite directions · numbered dots show each run at the chosen distance")) : null;

  // ----- position: slider + table -----
  const slider = el("input", { type: "range", min: "0", max: String(K), step: "1", value: String(Math.min(common, K)),
    class: "slider", "aria-label": "Distance into the run" });
  const posLabel = el("strong", {});
  const tbody = el("tbody");
  const posCard = el("section", { class: "card" },
    el("h2", {}, "Where was everyone?"),
    el("p", { class: "muted small" }, "Drag the slider or hover over the chart to pick a point."),
    el("div", { class: "slider-row" }, el("span", { class: "muted small" }, "0 km"), slider, el("span", { class: "muted small" }, fmt.km(maxM, 1))),
    el("p", { class: "pos-label" }, "At ", posLabel),
    el("div", { class: "table-wrap" }, el("table", { class: "compare-table" },
      el("thead", {}, el("tr", {},
        el("th", {}, "Run"), el("th", { class: "num" }, "Time"), el("th", { class: "num" }, "Pace"),
        el("th", { class: "num" }, "Heart rate"), el("th", { class: "num" }, "Height vs start"), el("th", { class: "num" }, "vs run 1"))),
      tbody)),
    el("p", { class: "muted small" }, "Time is the clock time since the start, including any stops. Pace is smoothed over about 30 seconds. " +
      "Height vs start is how far above (+) or below (−) your starting point each run was."));

  // ----- chart -----
  const metrics = {
    gap: { label: "Ahead / behind", ok: !!ref },
    pace: { label: "Pace", ok: true },
    hr: { label: "Heart rate", ok: series.some((s) => s && s.hr) },
    elevation: { label: "Elevation", ok: series.some((s) => s && s.elevation) },
  };
  if (!metrics[compareMetric].ok) compareMetric = "pace";
  const chartBox = el("div", { id: "compare-chart", class: "chart", tabindex: "0",
    "aria-label": "Comparison chart. Use left and right arrow keys to move along the runs." });
  const chartNote = el("p", { class: "muted small" });
  const chartCard = el("section", { class: "card" },
    el("div", { class: "list-head" },
      el("h2", {}, "Along the way"),
      el("div", { class: "segmented", role: "group", "aria-label": "Show" },
        ...Object.entries(metrics).filter(([, m]) => m.ok).map(([k, m]) =>
          el("button", { type: "button", class: "seg-btn", "aria-pressed": String(k === compareMetric),
            onclick: () => { compareMetric = k; drawChart(); setPosition(+slider.value); } }, m.label)))),
    chartNote, legend(runs), chartBox);

  page.append(
    notes.length ? el("section", { class: "card" }, el("h2", {}, "The verdict"),
      el("ul", { class: "notes" }, ...notes.map((n) => el("li", {}, el("span", { class: "note-icon", "aria-hidden": "true" }), el("span", {}, n))))) : null,
    mapCard, posCard, chartCard);

  // ----- behaviour -----
  function setPosition(k, fromChart) {
    slider.value = String(k);
    posLabel.textContent = fmt.km(k * 10, 2);
    const t0 = valueAt(ref, "t", k);
    tbody.replaceChildren(...runs.map((r, i) => {
      const s = series[i];
      const t = valueAt(s, "t", k);
      let vs = "—";
      if (i === 0) vs = "reference";
      else if (t != null && t0 != null) {
        const g = t - t0;
        vs = Math.abs(g) < 1 ? "level" : `${fmt.duration(Math.abs(g))} ${g < 0 ? "ahead" : "behind"}`;
      } else if (t != null) vs = "run 1 had finished";
      const finished = s && t == null;
      return el("tr", {},
        el("td", {}, el("span", { class: "run-cell" }, lineKey(i), runBadge(i), el("span", {}, fmt.day(r.start_date_local)))),
        finished
          ? el("td", { class: "muted", colspan: "5" }, `Finished at ${fmt.km(s.distance_m, 2)}`)
          : [el("td", { class: "num", "data-label": "Time" }, t == null ? "—" : fmt.duration(t)),
             el("td", { class: "num", "data-label": "Pace" }, valueAt(s, "pace", k) ? fmt.pace(valueAt(s, "pace", k)) : (t == null ? "—" : "stopped")),
             el("td", { class: "num", "data-label": "Heart rate" }, valueAt(s, "hr", k) ? `${valueAt(s, "hr", k)} bpm` : "—"),
             el("td", { class: "num", "data-label": "Height vs start" }, heightAt(i, k) != null ? signedM(heightAt(i, k)) : "—"),
             el("td", { class: "num vs", "data-label": "vs run 1" }, vs)]);
    }));
    if (compareChart && !fromChart) compareChart.show(k, true);
    // map dots
    compareDots.forEach((dot, i) => {
      const s = series[i];
      const ll = s && s.latlng && k < s.latlng.length ? s.latlng[k] : null;
      if (!dot) return;
      if (ll && ll[0] != null) { dot.setLatLng(ll); if (!compareMap.hasLayer(dot)) dot.addTo(compareMap); }
      else dot.remove();
    });
  }

  function drawChart() {
    document.querySelectorAll("#page-compare .seg-btn").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.textContent === metrics[compareMetric].label)));
    const pick = (key) => series.map((s, i) => ({
      ys: xs.map((_, k) => valueAt(s, key, k)), color: runColor(i), dash: RUN_STYLES[i].dash }));
    let opts;
    if (compareMetric === "gap") {
      chartNote.textContent = "How far ahead (up) or behind (down) each run was compared with run 1, in time, at the same distance.";
      const ys = series.map((s, i) => ({
        ys: xs.map((_, k) => { const a = valueAt(ref, "t", k), b = valueAt(s, "t", k); return a != null && b != null ? a - b : null; }),
        color: runColor(i), dash: RUN_STYLES[i].dash }));
      opts = { series: ys, includeZero: true, topLabel: "ahead ↑", bottomLabel: "behind ↓", tickSteps: [5, 10, 15, 30, 60, 120, 300, 600],
        yFormat: (v) => (v === 0 ? "0" : `${v > 0 ? "+" : "−"}${fmt.duration(Math.abs(v))}`), padLeft: 52 };
    } else if (compareMetric === "pace") {
      chartNote.textContent = "Pace smoothed over about 30 seconds. Faster is higher up; gaps are stops.";
      opts = { series: pick("pace"), invert: true, topLabel: "faster ↑", yFormat: (v) => fmt.pace(v, false) };
    } else if (compareMetric === "hr") {
      chartNote.textContent = "Heart rate in beats per minute.";
      opts = { series: pick("hr"), yFormat: (v) => `${Math.round(v)}` };
    } else {
      chartNote.textContent = "Height compared with where each run started, so every run begins at 0 m. " +
        "Up means you'd climbed above your start, down means you'd dropped below it. Lightly smoothed.";
      const ys = series.map((s, i) => ({ ys: xs.map((_, k) => heightAt(i, k)), color: runColor(i), dash: RUN_STYLES[i].dash }));
      opts = { series: ys, includeZero: true, topLabel: "above start ↑", bottomLabel: "below start ↓",
        yFormat: (v) => (v === 0 ? "0" : `${v > 0 ? "+" : "−"}${Math.abs(Math.round(v))}`) };
    }
    compareChart = lineChart(chartBox, { ...opts, xs, keepOnLeave: true,
      xFormat: (v) => `${+v.toFixed(1)} km`, onHover: (k) => k != null && setPosition(k, true) });
  }

  if (hasMap) drawCompareMap(runs, series, overlaps);
  drawChart();
  redrawCompareChart = () => { drawChart(); setPosition(+slider.value); };
  slider.addEventListener("input", () => setPosition(+slider.value));
  setPosition(+slider.value);
}

function drawCompareMap(runs, series, overlaps) {
  compareMap = L.map("compare-map", { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(compareMap);
  const band = cssVar("--band");
  const bounds = [];
  // Shared stretches first, as a wide wash underneath the route lines
  series.forEach((s, i) => {
    for (const [a, b, kind] of overlaps.segments[i] || []) {
      const pts = s.latlng.slice(a, b + 1).filter((p) => p[0] != null);
      L.polyline(pts, { color: band, weight: 16, opacity: 0.45, lineCap: "butt",
        dashArray: kind === "opposite" ? "14 8" : null, interactive: false }).addTo(compareMap);
    }
  });
  // Then each route: a light casing so it stands out from the streets, then the coloured line
  series.forEach((s, i) => {
    if (!s || !s.latlng) return;
    const segs = routeSegments(s.latlng);
    segs.forEach((seg) => bounds.push(...seg));
    L.polyline(segs, { color: cssVar("--surface"), weight: 7, opacity: 0.9, interactive: false }).addTo(compareMap);
    L.polyline(segs, { color: runColor(i), weight: 4, dashArray: RUN_STYLES[i].leafletDash, interactive: false }).addTo(compareMap);
  });
  if (bounds.length) compareMap.fitBounds(bounds, { padding: [20, 20] });
  compareDots = series.map((s, i) => (s && s.latlng
    ? L.marker([0, 0], { icon: L.divIcon({ className: "compare-dot", iconSize: [24, 24],
        html: `<span style="background:${runColor(i)};color:${RUN_STYLES[i].ink}">${i + 1}</span>` }),
        title: `Run ${i + 1}`, keyboard: false, zIndexOffset: 1000 - i })
    : null));
}

let redrawCompareChart = null, resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (redrawCompareChart && !$("page-compare").hidden) redrawCompareChart();
  }, 200);
});

// ---------------------------------------------------------------- shared roads

const GRID_M = 10;        // the server lines runs up every 10 m
const OVERLAP_M = 20;     // routes within 20 m count as the same road
const MIN_SHARED_M = 50;  // ignore brief touches, e.g. crossing at a junction

// Where do routes run along the same roads, and in which direction?
// Returns { segments: per run [[startIndex, endIndex, "same"|"opposite"], …],
//           pairs: [{ a, b, same_m, opposite_m }] }.
// Crossing at a junction doesn't count: the two routes must be heading along the same line.
function findOverlaps(series) {
  const first = series.flatMap((s) => (s && s.latlng ? s.latlng : [])).find((p) => p && p[0] != null);
  if (!first) return { segments: series.map(() => null), pairs: [] };
  const kx = 111320 * Math.cos((first[0] * Math.PI) / 180), ky = 110540;

  const pts = series.map((s) => (s && s.latlng
    ? s.latlng.map((ll) => (ll && ll[0] != null ? [(ll[1] - first[1]) * kx, (ll[0] - first[0]) * ky] : null))
    : null));
  const grids = pts.map((p) => {
    if (!p) return null;
    const g = new Map();
    p.forEach((xy, k) => {
      if (!xy) return;
      const cell = `${Math.floor(xy[0] / OVERLAP_M)},${Math.floor(xy[1] / OVERLAP_M)}`;
      if (!g.has(cell)) g.set(cell, []);
      g.get(cell).push(k);
    });
    return g;
  });
  const heading = (p, k) => {
    const a = p[Math.max(k - 3, 0)], b = p[Math.min(k + 3, p.length - 1)];
    if (!a || !b) return null;
    const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy);
    return len > 1 ? [dx / len, dy / len] : null;
  };

  const pairMetres = {};
  const segments = pts.map((p, i) => {
    if (!p) return null;
    const kinds = new Array(p.length).fill(null);
    pts.forEach((q, j) => {
      if (j === i || !q) return;
      p.forEach((xy, k) => {
        if (!xy) return;
        const cx = Math.floor(xy[0] / OVERLAP_M), cy = Math.floor(xy[1] / OVERLAP_M);
        let best = OVERLAP_M * OVERLAP_M, bestK = null;
        for (let gx = cx - 1; gx <= cx + 1; gx++)
          for (let gy = cy - 1; gy <= cy + 1; gy++)
            for (const k2 of grids[j].get(`${gx},${gy}`) || []) {
              const d2 = (q[k2][0] - xy[0]) ** 2 + (q[k2][1] - xy[1]) ** 2;
              if (d2 <= best) { best = d2; bestK = k2; }
            }
        if (bestK == null) return;
        const h1 = heading(p, k), h2 = heading(q, bestK);
        if (!h1 || !h2) return;
        const dot = h1[0] * h2[0] + h1[1] * h2[1];
        const kind = dot >= 0.5 ? "same" : dot <= -0.5 ? "opposite" : null;   // otherwise just crossing
        if (!kind) return;
        if (i < j) pairMetres[`${i},${j},${kind}`] = (pairMetres[`${i},${j},${kind}`] || 0) + GRID_M;
        if (kinds[k] !== "opposite") kinds[k] = kind;   // "opposite" is the more interesting label
      });
    });
    // Turn per-point labels into stretches, dropping brief touches
    const segs = [];
    let start = null;
    for (let k = 0; k <= kinds.length; k++) {
      const kind = k < kinds.length ? kinds[k] : null;
      if (start != null && kind !== kinds[start]) {
        if ((k - start) * GRID_M >= MIN_SHARED_M) segs.push([start, k - 1, kinds[start]]);
        start = null;
      }
      if (kind && start == null) start = k;
    }
    return segs;
  });

  const pairs = [];
  for (let a = 0; a < series.length; a++)
    for (let b = a + 1; b < series.length; b++) {
      const same_m = pairMetres[`${a},${b},same`] || 0, opposite_m = pairMetres[`${a},${b},opposite`] || 0;
      if (same_m + opposite_m >= MIN_SHARED_M) pairs.push({ a, b, same_m, opposite_m });
    }
  return { segments, pairs };
}
