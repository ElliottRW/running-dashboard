// Personal bests and the run list.

const DEFAULT_TAGS = ["easy", "long run", "group run", "intervals", "tempo", "5k attempt", "parkrun", "race"];
let runsData = null;

async function renderRuns() {
  runsData = await DATA.runs();
  // The filter only offers tags you've used on runs; suggestions include every tag
  renderTagOptions(runsData.tags, [...new Set(runsData.runs.map((r) => r.tag).filter(Boolean))].sort());
  renderRunList();
}

// ---------- personal bests ----------

function renderPBs(pbs, runs) {
  const counted = runs.filter((r) => r.stats && !r.duplicate_of);
  $("pbs").hidden = counted.length === 0;
  const grid = $("pb-grid");
  grid.replaceChildren();
  const longest = pbs.longest.best;

  for (const [key, label] of [["1k", "Fastest 1 km"], ["5k", "Fastest 5 km"], ["10k", "Fastest 10 km"]]) {
    const pb = pbs[key];
    const b = pb.best;
    if (!b) {
      const need = pb.metres / 1000;
      grid.append(el("div", { class: "pb empty" },
        el("div", { class: "pb-label" }, label),
        el("div", { class: "pb-value muted" }, "Not yet"),
        el("div", { class: "pb-sub" }, longest
          ? `Needs a ${need} km run – longest so far ${fmt.km(longest.distance_m)}.`
          : `Needs a ${need} km GPS run.`)));
      continue;
    }
    grid.append(pbCard(label, fmt.duration(b.seconds), fmt.pace(b.seconds / (pb.metres / 1000)), b, pb,
      pb.previous && pb.previous.run_id !== b.run_id
        ? `${fmt.duration(pb.previous.seconds - b.seconds)} faster than before (${fmt.duration(pb.previous.seconds)})`
        : null,
      b.start_m >= 100 ? `from ${fmt.km(b.start_m, 1)}` : null));
  }

  const L = pbs.longest;
  if (L.best) {
    grid.append(pbCard("Longest run", fmt.km(L.best.distance_m), null, L.best, L,
      L.previous && L.previous.run_id !== L.best.run_id
        ? `${fmt.km(L.best.distance_m - L.previous.distance_m)} further than before (${fmt.km(L.previous.distance_m)})`
        : null));
  }
}

function pbCard(label, value, sub, best, pb, improvement, where) {
  return el("div", { class: `pb${pb.is_new ? " is-new" : ""}` },
    el("div", { class: "pb-label" }, label, pb.is_new ? el("span", { class: "new-pb" }, "New best 🎉") : null),
    el("div", { class: "pb-value" }, value),
    sub ? el("div", { class: "pb-sub" }, sub) : null,
    pb.is_new && improvement ? el("div", { class: "pb-sub strong" }, improvement) : null,
    el("div", { class: "pb-sub muted" }, `${fmt.day(best.date)} · `, el("a", { href: `#/run/${best.run_id}` }, best.name), where ? ` · ${where}` : ""));
}

// ---------- run list ----------

function renderTagOptions(tags, runTags = tags) {
  const all = [...new Set([...tags, ...DEFAULT_TAGS])];
  $("tag-options").replaceChildren(...all.map((t) => el("option", { value: t })));
  const sel = $("tag-filter");
  const current = sel.value;
  sel.replaceChildren(el("option", { value: "" }, "all runs"),
    ...runTags.map((t) => el("option", { value: t }, `tagged “${t}”`)),
    el("option", { value: "__untagged" }, "untagged"));
  sel.value = [...sel.options].some((o) => o.value === current) ? current : "";
}

function runBadges(r) {
  const b = [];
  if (r.duplicate_of) b.push(["Duplicate – not counted", "Looks like the same run was uploaded to Strava twice. It's left out of your bests and totals. You can delete the extra copy on Strava."]);
  if (r.sport_type === "TrailRun") b.push(["Trail"]);
  if (r.private) b.push(["Private", "Only visible to you on Strava"]);
  if (r.streams_status === "pending") b.push(["Details downloading…"]);
  if (r.streams_status === "missing") b.push(["No detailed data", r.streams_note]);
  // A gym session or yoga class never has GPS – only mention it when there was some distance
  const onTheMove = isRunType(r) || (r.summary_distance || 0) > 0;
  if (r.streams_status === "done" && !r.has_gps && onTheMove) b.push([r.trainer ? (isRunType(r) ? "Treadmill – no GPS" : "Indoor – no GPS") : "No GPS", r.streams_note || "No map or GPS-based stats for this one."]);
  if (r.streams_status === "done" && !r.has_hr) b.push(["No heart rate", "Your watch didn't record heart rate on this one."]);
  return b.map(([text, title]) => el("span", { class: "badge", title }, text));
}

const isRunType = (r) => r.sport_type === "Run" || r.sport_type === "TrailRun";

function renderRunList() {
  const { runs } = runsData;
  $("runs").hidden = runs.length === 0;
  const filter = $("tag-filter").value;
  const shown = runs.filter((r) => !filter || (filter === "__untagged" ? !r.tag : r.tag === filter));
  $("run-count").textContent = filter ? `(${shown.length} of ${runs.length})` : `(${runs.length})`;
  const newIds = new Set(runsData.new_ids);

  $("run-rows").replaceChildren(...shown.map((r) => {
    const s = r.stats || {};
    const noHr = r.streams_status === "done" && !r.has_hr;
    const row = el("tr", { class: `run-row${r.duplicate_of ? " is-dup" : ""}` },
      el("td", { class: "date", "data-label": "Date" },
        el("div", {}, fmt.day(r.start_date_local)),
        el("div", { class: "muted small" }, fmt.clock(r.start_date_local))),
      el("td", { class: "name-cell" },
        el("div", { class: "run-name" }, el("a", { href: `#/run/${r.id}` }, r.name), newIds.has(r.id) ? el("span", { class: "new-run" }, "new") : null),
        el("div", { class: "badges" }, tagChip(r), ...runBadges(r))),
      el("td", { class: "num", "data-label": "Distance", title: s.distance_source === "summary" ? "Strava's distance – no recording to measure from" : null },
        pill("distance", fmt.km(s.distance_m ?? r.summary_distance))),
      el("td", { class: "num", "data-label": "Time" }, pill("time", fmt.duration(s.moving_s ?? r.moving_time))),
      el("td", { class: "num", "data-label": "Pace" }, pill("pace", fmt.pace(s.pace_s_per_km))),
      el("td", { class: "num", "data-label": "Avg HR" }, pill("hr", noHr ? "—" : fmt.bpm(s.avg_hr))),
      el("td", { class: "num", "data-label": "Max HR" }, pill("maxhr", noHr ? "—" : fmt.bpm(s.max_hr))),
      el("td", { class: "num", "data-label": "Climb" }, pill("climb", fmt.metres(s.elev_gain_m))));
    row.addEventListener("click", (e) => {
      if (e.target.closest("button, input, a, select")) return;   // let the tag chip and link do their own thing
      if (window.getSelection().toString()) return;                // don't jump away while selecting text
      location.hash = `#/run/${r.id}`;
    });
    return row;
  }));
}

// A number in its stat's colour ("—" stays plain)
function pill(kind, text) {
  return el("span", { class: `pill stat-${kind}${text === "—" ? " empty" : ""}` }, text);
}

// ---------- your own tags ----------

function tagChip(r) {
  // On the website tags are read-only (it has no server to save them to)
  if (!DATA.canEditTags) return r.tag ? el("span", { class: "tag" }, r.tag) : null;
  const chip = el("button", { class: `tag${r.tag ? "" : " empty"}`, type: "button",
    title: r.tag ? "Change or remove this tag" : "Add your own label, e.g. easy or group run" },
    r.tag ? r.tag : "+ tag");
  chip.addEventListener("click", () => editTag(chip, r));
  return chip;
}

function editTag(chip, r) {
  const input = el("input", { class: "tag-input", list: "tag-options", value: r.tag || "",
    placeholder: "e.g. easy", maxlength: "40", "aria-label": `Tag for ${r.name}` });
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    if (save && input.value.trim() !== (r.tag || "")) {
      const res = await getJSON(`/api/runs/${r.id}/tag`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag: input.value }) });
      r.tag = res.tag;
      input.replaceWith(tagChip(r));
      renderRuns();          // refresh filters and suggestions
    } else {
      input.replaceWith(tagChip(r));
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
  chip.replaceWith(input);
  input.focus();
}

document.addEventListener("DOMContentLoaded", () => {
  $("tag-filter").addEventListener("change", renderRunList);
});
