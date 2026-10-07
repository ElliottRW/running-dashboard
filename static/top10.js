// Overview: your 10 fastest 1 km / 5 km / 10 km efforts, plotted against every other run,
// with a line for the average of your last few so you can see which way you're heading.

const TOP_N = 10;
const AVG_OF = 5;             // the trend line averages your last 5 efforts at that distance
let topDistance = "5k";

function renderTop10(runs) {
  const box = $("top10");
  // Same runs the PB cards count: GPS runs (treadmills have no best efforts), no duplicates.
  const effortsFor = (key) => runs
    .map((r) => ({ run: r, e: (r.stats.best_efforts || {})[key] }))
    .filter((p) => p.e)
    .map(({ run, e }) => ({ id: run.id, name: run.name, date: run.start_date_local, when: runDate(run),
      seconds: e.seconds, metres: { "1k": 1000, "5k": 5000, "10k": 10000 }[key] }))
    .sort((a, b) => a.when - b.when);

  const counts = Object.fromEntries(["1k", "5k", "10k"].map((k) => [k, effortsFor(k).length]));
  box.hidden = counts["1k"] === 0;
  if (box.hidden) return;
  if (!counts[topDistance]) topDistance = counts["5k"] ? "5k" : "1k";

  const draw = () => {
    box.querySelectorAll(".seg-btn").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.d === topDistance));
      b.disabled = !counts[b.dataset.d];
      b.title = counts[b.dataset.d] ? "" : `Needs a run of at least ${b.textContent}`;
    });
    const efforts = effortsFor(topDistance);
    const ranked = [...efforts].sort((a, b) => a.seconds - b.seconds);
    ranked.forEach((p, i) => { p.rank = i + 1; });
    const top = ranked.slice(0, TOP_N);
    efforts.forEach((p, i) => {
      const last = efforts.slice(Math.max(0, i - AVG_OF + 1), i + 1);
      p.avg = i >= 2 ? last.reduce((a, q) => a + q.seconds, 0) / last.length : null;
    });
    $("top10-note").textContent = efforts.length > TOP_N
      ? `Each dot is the fastest ${topDistance.replace("k", " km")} inside one run. Filled dots are your top ${TOP_N}.`
      : `Each dot is the fastest ${topDistance.replace("k", " km")} inside one run. Once you have more than ${TOP_N}, only your top ${TOP_N} stay filled in.`;
    topChart($("top10-chart"), efforts);
    $("top10-rows").replaceChildren(...top.map((p) => el("tr", {},
      el("td", { class: "num rank" }, `${p.rank}`),
      el("td", { class: "num" }, el("strong", {}, fmt.duration(p.seconds))),
      el("td", { class: "num" }, fmt.pace(p.seconds / (p.metres / 1000))),
      el("td", { class: "date" }, fmt.day(p.date)),
      el("td", {}, el("a", { href: `#/run/${p.id}` }, p.name)))));
  };
  box.querySelectorAll(".seg-btn").forEach((b) => { b.onclick = () => { topDistance = b.dataset.d; draw(); }; });
  box._redraw = draw;
  draw();
}

function topChart(container, pts) {
  const W = Math.max(container.clientWidth, 280), H = 250;
  const pad = { l: 46, r: 16, t: 14, b: 28 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;

  // Faster is higher, like the pace charts: the y axis runs from slowest (bottom) to fastest (top).
  const ys = pts.flatMap((p) => [p.seconds, p.avg]).filter((v) => v != null);
  const span = Math.max(...ys) - Math.min(...ys);
  const ticks = niceTicks(Math.min(...ys) - span * 0.08, Math.max(...ys) + span * 0.08, 4, [1, 2, 5, 10, 15, 30, 60, 120, 300]);
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const sy = (s) => pad.t + ((s - lo) / (hi - lo || 1)) * ih;

  const DAY = 86400000;
  let t0 = pts[0].when.getTime(), t1 = pts[pts.length - 1].when.getTime();
  const edge = Math.max((t1 - t0) * 0.04, DAY / 2);   // keep the first and last dots off the edges
  t0 -= edge; t1 += edge;
  const sx = (d) => pad.l + ((d.getTime() - t0) / (t1 - t0)) * iw;

  // Date ticks on whole days, spaced so they never crowd
  const maxTicks = W < 500 ? 4 : 7;
  const stepDays = [1, 2, 3, 7, 14, 28, 61, 91, 182, 365].find((s) => (t1 - t0) / DAY / s <= maxTicks) || 365;
  const xTicks = [];
  for (let d = startOfDay(new Date(t0 + DAY)); d.getTime() <= t1; d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + stepDays)) xTicks.push(d);
  const dayLabel = (d) => d.toLocaleDateString(undefined, { day: "numeric", month: "short" });

  const top = new Set(pts.filter((p) => p.rank <= TOP_N).map((p) => p.id));
  const withAvg = pts.filter((p) => p.avg != null);
  const avgPath = withAvg.map((p, i) => `${i ? "L" : "M"}${sx(p.when).toFixed(1)},${sy(p.avg).toFixed(1)}`).join("");
  const best = pts.find((p) => p.rank === 1);
  const bx = sx(best.when), by = sy(best.seconds);
  const bestAnchor = bx > W - 80 ? "end" : bx < pad.l + 40 ? "start" : "middle";

  container.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Your fastest efforts over time">
      ${ticks.map((t) => `<line class="grid" x1="${pad.l}" x2="${W - pad.r}" y1="${sy(t)}" y2="${sy(t)}"/>
        <text class="axis" x="${pad.l - 6}" y="${sy(t) + 4}" text-anchor="end">${fmt.duration(t)}</text>`).join("")}
      ${xTicks.map((d) => `<text class="axis" x="${sx(d)}" y="${H - 8}" text-anchor="middle">${dayLabel(d)}</text>`).join("")}
      <text class="axis" x="${pad.l + 4}" y="${pad.t + 10}">faster ↑</text>
      ${avgPath ? `<path class="line avg-line" d="${avgPath}"/>` : ""}
      ${pts.map((p, i) => `<circle class="effort-dot${top.has(p.id) ? " top" : ""}" data-i="${i}" cx="${sx(p.when).toFixed(1)}" cy="${sy(p.seconds).toFixed(1)}" r="5"/>`).join("")}
      <text class="best-label" x="${bx}" y="${by - 11}" text-anchor="${bestAnchor}">Best ${fmt.duration(best.seconds)}</text>
      <circle class="hover-ring" r="9" visibility="hidden"/>
    </svg>`;   // only numbers and our own fixed labels go into this markup – run names go in the tooltip via textContent
  const tip = el("div", { class: "tooltip", hidden: true });
  container.append(tip);

  const svgEl = container.querySelector("svg");
  const ring = svgEl.querySelector(".hover-ring");
  let current = null;
  function show(i) {
    current = i;
    if (i == null) { ring.setAttribute("visibility", "hidden"); tip.hidden = true; return; }
    const p = pts[i], cx = sx(p.when), cy = sy(p.seconds);
    ring.setAttribute("cx", cx); ring.setAttribute("cy", cy); ring.setAttribute("visibility", "visible");
    tip.replaceChildren(...[
      el("div", { class: "tip-head" }, el("strong", {}, fmt.duration(p.seconds)), ` · ${fmt.pace(p.seconds / (p.metres / 1000))}`),
      el("div", { class: "tip-row" }, `#${p.rank} fastest of ${pts.length} · `, fmt.day(p.date)),
      el("div", { class: "tip-row" }, p.name),
      p.avg != null ? el("div", { class: "tip-row" }, el("strong", {}, fmt.duration(p.avg)), ` average of last ${Math.min(AVG_OF, i + 1)}`) : null,
    ].filter(Boolean));
    tip.hidden = false;
    const left = cx + 14 + tip.offsetWidth > W ? cx - 14 - tip.offsetWidth : cx + 14;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${Math.max(0, Math.min(cy - 20, H - tip.offsetHeight))}px`;
  }
  // The pointer only has to be closest to a dot, not on it – dots are small targets on a phone.
  const nearest = (evt) => {
    const r = svgEl.getBoundingClientRect();
    const x = (evt.clientX - r.left) * (W / r.width), y = (evt.clientY - r.top) * (H / r.height);
    let bestI = null, bestD = 40 * 40;
    pts.forEach((p, i) => {
      const d = (sx(p.when) - x) ** 2 + (sy(p.seconds) - y) ** 2;
      if (d < bestD) { bestD = d; bestI = i; }
    });
    return bestI;
  };
  svgEl.addEventListener("pointermove", (e) => show(nearest(e)));
  svgEl.addEventListener("pointerleave", () => show(null));
  svgEl.addEventListener("click", (e) => { const i = nearest(e); if (i != null) location.hash = `#/run/${pts[i].id}`; });
  container.onkeydown = (e) => {
    if (e.key === "ArrowRight") { show(Math.min((current ?? -1) + 1, pts.length - 1)); e.preventDefault(); }
    if (e.key === "ArrowLeft") { show(Math.max((current ?? 1) - 1, 0)); e.preventDefault(); }
    if (e.key === "Enter" && current != null) location.hash = `#/run/${pts[current].id}`;
    if (e.key === "Escape") show(null);
  };
  container.onblur = () => show(null);
}

window.addEventListener("resize", () => {
  const box = $("top10");
  if (box && !box.hidden && box._redraw && !$("page-overview").hidden) box._redraw();
});
