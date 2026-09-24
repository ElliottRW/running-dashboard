// The app shell: Strava connection, sync progress, and loading the pages.

const $ = (id) => document.getElementById(id);
let polling = null;

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  return r.json();
}

function timeAgo(iso) {
  if (!iso) return "never";
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function clockTime(iso) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function showNotice(kind, text) {
  const n = $("notice");
  n.className = `notice ${kind}`;
  n.textContent = text;
  n.hidden = !text;
}

// Messages after coming back from Strava's login page
const LOGIN_MESSAGES = {
  config: ["error", "Add your Strava Client ID and Secret to the .env file first (see the README)."],
  denied: ["warn", "Strava login was cancelled, so nothing was connected. Try again whenever you're ready."],
  state: ["error", "That login link had expired. Please click “Connect to Strava” again."],
  offline: ["error", "Couldn't reach Strava to finish logging in. Check your internet and try again."],
};

function handleLoginRedirect() {
  const params = new URLSearchParams(location.search);
  const err = params.get("error");
  if (err && LOGIN_MESSAGES[err]) showNotice(...LOGIN_MESSAGES[err]);
  if (params.get("connected")) showNotice("ok", "Connected to Strava! Your runs are being downloaded now.");
  if (err || params.get("connected")) history.replaceState(null, "", "/" + location.hash);
}

function renderSync(status) {
  const s = status.sync;
  const card = $("card-sync");
  const showCard = s.running || s.phase === "paused" || s.error_kind === "daily_limit";
  card.hidden = !showCard;
  $("sync-btn").disabled = s.running;
  $("sync-btn").textContent = s.running ? "Syncing…" : "Sync";
  $("last-sync").textContent = `Last synced: ${timeAgo(status.last_sync)}`;

  if (!showCard) return;
  const bar = $("sync-bar");
  const titles = { listing: "Finding your runs", streams: "Downloading run details",
                   paused: "Paused for Strava's limit", starting: "Starting sync", error: "Paused until tomorrow" };
  $("sync-title").textContent = titles[s.phase] || "Syncing…";
  if (s.phase === "streams" && s.total) {
    bar.classList.remove("indeterminate");
    bar.style.width = `${Math.round((s.done / s.total) * 100)}%`;
    $("sync-count").textContent = `${s.done} of ${s.total}`;
  } else {
    bar.classList.toggle("indeterminate", s.running && s.phase !== "paused");
    bar.style.width = s.running ? "" : "0";
    $("sync-count").textContent = "";
  }
  let msg = s.message || "";
  if (s.paused_until) msg += ` (Resumes at ${clockTime(s.paused_until)}.)`;
  $("sync-msg").textContent = msg;
  const u = s.usage;
  $("sync-usage").textContent = u && u.daily_used
    ? `Strava requests used: ${u.short_used}/${u.short_limit} this 15 min · ${u.daily_used}/${u.daily_limit} today`
    : "";
}

function renderErrors(status) {
  const s = status.sync;
  if (s.error && s.error_kind !== "daily_limit") showNotice("error", s.error);
  else if (!status.scope_ok)
    showNotice("warn", "Strava wasn't given permission to see your private activities, so private runs will be missing. " +
      "To fix it, click “Connect to Strava” again and leave that box ticked.");
}

async function refresh() {
  const status = await DATA.status();
  if (status.static) {
    // Website: no Strava connection here – GitHub updates the data once a day
    $("last-sync").textContent = `Updated ${timeAgo(status.last_sync)}`;
    return status;
  }
  $("card-config").hidden = status.configured;
  $("card-connect").hidden = !status.configured || status.connected;
  $("sync-btn").hidden = !status.connected;
  $("athlete").textContent = status.athlete_name ? `Connected to Strava as ${status.athlete_name}` : "";
  renderSync(status);
  renderErrors(status);
  return status;
}

async function startSync() {
  showNotice("", "");
  await fetch("/api/sync", { method: "POST" });
  poll();
}

// Re-draw whichever list-style page is showing, after new runs arrive
async function refreshPages() {
  await renderRuns();
  if (!$("page-overview").hidden) await renderOverview();
}

function poll() {
  clearInterval(polling);
  let lastPending = -1;
  polling = setInterval(async () => {
    let status;
    try { status = await refresh(); } catch { return; }   // server stopped – try again next tick
    // Refresh the list as runs arrive, not on every tick
    if (status.counts.runs + status.counts.pending !== lastPending) {
      lastPending = status.counts.runs + status.counts.pending;
      refreshPages();
    }
    if (!status.sync.running) {
      clearInterval(polling);
      refreshPages();
      if (status.sync.phase === "done" && status.sync.new_runs) showNotice("ok", status.sync.message);
    }
  }, 1500);
}

// Simple page switching using the part of the address after "#"
const PAGES = ["overview", "list", "run", "compare"];
function route() {
  if (!DATA.isUnlocked()) return;
  const run = location.hash.match(/^#\/run\/(\d+)/);
  const cmp = location.hash.match(/^#\/compare\/?([\d,]*)/);
  const page = run ? "run" : cmp ? "compare" : location.hash.startsWith("#/runs") ? "list" : "overview";
  for (const p of PAGES) $(`page-${p}`).hidden = p !== page;
  document.querySelectorAll(".nav-link").forEach((a) =>
    a.setAttribute("aria-current", a.dataset.page === page || (page === "run" && a.dataset.page === "list") ? "page" : "false"));
  if (page !== "run" && runMap) { runMap.remove(); runMap = null; }
  if (page !== "compare" && compareMap) { compareMap.remove(); compareMap = null; }
  if (page === "overview") renderOverview();
  if (page === "list") renderRuns();
  if (run) renderRunPage(Number(run[1]));
  if (cmp) renderComparePage([...new Set(cmp[1].split(",").filter(Boolean).map(Number))].slice(0, MAX_COMPARE));
  window.scrollTo(0, 0);
}

// ---------- website: password screen ----------

function showLock() {
  $("card-lock").hidden = false;
  $("lock-password").focus();
  $("lock-form").onsubmit = async (e) => {
    e.preventDefault();
    const btn = $("lock-form").querySelector("button");
    btn.disabled = true; btn.textContent = "Unlocking…";
    $("lock-msg").textContent = "";
    try {
      await DATA.unlock($("lock-password").value, $("lock-remember").checked);
      $("card-lock").hidden = true;
      $("lock-password").value = "";
      startWebsite();
    } catch (err) {
      $("lock-msg").textContent = err.message === "wrong-password"
        ? "That password didn't work. Check it and try again."
        : "Couldn't load the dashboard – check your internet connection and try again.";
    } finally {
      btn.disabled = false; btn.textContent = "Unlock";
    }
  };
}

async function startWebsite() {
  document.body.classList.remove("locked");
  const upd = $("update-btn");
  if (CONFIG.workflowUrl) {
    upd.href = CONFIG.workflowUrl;
    upd.hidden = false;
    upd.title = "Opens GitHub. Tap “Run workflow”, wait about 2 minutes, then reload this page.";
  }
  $("tag-hint").textContent = "Tags are added in the Mac version of the app.";
  $("athlete").replaceChildren(
    el("button", { type: "button", class: "btn link small", onclick: () => { DATA.forget(); location.reload(); } }, "Lock this device"));
  await refresh();
  route();
}

async function boot() {
  window.addEventListener("hashchange", route);
  if (DATA.isStatic) {
    document.body.classList.add("locked");
    if (await DATA.unlockRemembered()) startWebsite();
    else showLock();
    return;
  }
  handleLoginRedirect();
  route();
  $("sync-btn").addEventListener("click", startSync);
  const status = await refresh();
  if (status.sync.running) poll();
  else if (status.cloud_mode) {
    // The website (GitHub) does the syncing now – two copies refreshing the same
    // Strava login can knock each other out, so don't sync here unless asked.
    $("athlete").textContent += " · The website handles syncing. Run “python cloud.py pull” to get its latest runs here.";
  } else if (status.connected) startSync();   // sync automatically every time the dashboard is opened
}

boot();
