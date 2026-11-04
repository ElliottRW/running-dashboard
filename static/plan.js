// The training plan: this week and what's next, with sessions to tick off. The plan itself
// lives in the locked data ("plan" in the database). Ticks stay in this browser, as the
// website has no server to save them to.

const PLAN_TICKS = "running-dashboard-plan-ticks";

function loadPlanTicks() {
  try { return JSON.parse(localStorage.getItem(PLAN_TICKS)) || {}; } catch { return {}; }
}

function savePlanTicks(ticks) {
  try { localStorage.setItem(PLAN_TICKS, JSON.stringify(ticks)); } catch { /* private browsing */ }
}

const planDate = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
const planDay = (d, opts) => d.toLocaleDateString("en-GB", opts);

function planWeekDays(week) {
  const monday = planDate(week.monday);
  return [...Array(7)].map((_, i) => new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i));
}

function planSessionRow(session, ticks, onChange) {
  const done = () => (session.id in ticks ? ticks[session.id] : !!session.done);
  const box = el("input", { type: "checkbox", class: "plan-box", id: `plan-${session.id}` });
  box.checked = done();
  const row = el("label", { class: `plan-session${done() ? " ticked" : ""}${session.race ? " race" : ""}` }, box,
    el("span", { class: "plan-session-text" },
      el("span", { class: "plan-what" }, session.what),
      el("span", { class: "plan-how muted small" }, `${session.outdoors ? "Outdoors" : "Treadmill"} · ${session.how}`)));
  box.onchange = () => {
    ticks[session.id] = box.checked;
    savePlanTicks(ticks);
    row.classList.toggle("ticked", box.checked);
    onChange();
  };
  return row;
}

function planWeekCard(week, thisWeek, ticks, onChange) {
  const days = planWeekDays(week).map((day, i) => {
    const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    const sessions = week.sessions.filter((s) => s.date === iso);
    const classes = (week.gym && week.gym[i]) || [];
    return el("div", { class: "plan-day" },
      el("div", { class: "plan-date muted small" }, planDay(day, { weekday: "short", day: "numeric", month: "short" })),
      el("div", { class: "plan-items" },
        ...classes.map((c) => el("div", { class: "plan-class small" }, c)),
        ...sessions.map((s) => planSessionRow(s, ticks, onChange)),
        !classes.length && !sessions.length ? el("div", { class: "plan-rest muted small" }, "Rest") : null));
  });
  return el("section", { class: `plan-week${thisWeek ? " now" : ""}` },
    el("div", { class: "plan-week-head" },
      el("h3", {}, thisWeek ? "This week" : `Week of ${planDay(planDate(week.monday), { day: "numeric", month: "short" })}`),
      week.tag ? el("span", { class: "plan-tag small" }, week.tag) : null,
      el("span", { class: "plan-tag small" }, week.label),
      week.km ? el("span", { class: "plan-km small" }, `${week.km} km`) : null),
    ...days);
}

async function renderPlan() {
  const { plan } = await DATA.runs();
  const page = $("page-plan");
  if (!plan) {
    page.replaceChildren(el("section", { class: "card" }, el("h2", {}, "Training plan"),
      el("p", { class: "muted small" }, "There's no training plan yet.")));
    return;
  }
  const ticks = loadPlanTicks();
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const sessions = plan.weeks.flatMap((w) => w.sessions);
  const isDone = (s) => (s.id in ticks ? ticks[s.id] : !!s.done);
  const daysLeft = Math.round((planDate(plan.race) - today) / 864e5);

  const progress = el("p", { class: "muted small" });
  const next = el("div", { class: "plan-next" });
  const update = () => {
    progress.textContent = `${sessions.filter(isDone).length} of ${sessions.length} sessions done. Ticks are saved on this device.`;
    const upcoming = sessions.find((s) => planDate(s.date) >= today && !isDone(s));
    next.replaceChildren(...(upcoming ? [
      el("div", { class: "muted small" }, `Next: ${planDay(planDate(upcoming.date), { weekday: "long", day: "numeric", month: "short" })}`),
      el("div", { class: "plan-what" }, upcoming.what),
      el("div", { class: "muted small" }, upcoming.how),
    ] : [el("div", { class: "plan-what" }, "All sessions are ticked off.")]));
  };
  update();

  const startsOn = (w) => planDate(w.monday);
  const endsBefore = (w) => new Date(startsOn(w).getFullYear(), startsOn(w).getMonth(), startsOn(w).getDate() + 7);
  const past = plan.weeks.filter((w) => endsBefore(w) <= today);
  const current = plan.weeks.filter((w) => endsBefore(w) > today);
  const isNow = (w) => startsOn(w) <= today && today < endsBefore(w);

  page.replaceChildren(
    el("section", { class: "card" },
      el("h2", {}, plan.title),
      el("p", { class: "muted small" }, `${daysLeft > 0 ? `${daysLeft} days to go. ` : ""}${plan.summary}`),
      next, progress),
    el("section", { class: "card plan-weeks" },
      ...current.map((w) => planWeekCard(w, isNow(w), ticks, update)),
      past.length ? el("details", { class: "plan-past" }, el("summary", { class: "small" }, `Earlier weeks (${past.length})`),
        ...past.map((w) => planWeekCard(w, false, ticks, update))) : null),
    ...(plan.guide || []).map((g) => el("section", { class: "card" },
      el("h2", {}, g.title),
      el("div", { class: "plan-guide small" }, ...g.rows.map(([a, b]) => el("div", { class: "plan-guide-row" }, el("span", {}, a), el("span", { class: "num" }, b)))),
      g.note ? el("p", { class: "muted small plan-note" }, g.note) : null)));
}

// The Plan link only appears once there's a plan to show
let planLinkChecked = false;
async function showPlanLink() {
  if (planLinkChecked) return;
  planLinkChecked = true;
  const { plan } = await DATA.runs();
  $("nav-plan").hidden = !plan;
}
