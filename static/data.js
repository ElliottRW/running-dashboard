// Where the page gets its data from.
//
// "server" mode (on your Mac): asks the little Python server.
// "static" mode (the GitHub Pages website): downloads password-locked files and unlocks
// them in your browser. The password never leaves your device.

const CONFIG = window.SITE_CONFIG || { mode: "server" };
const IS_STATIC = CONFIG.mode === "static";

const DATA = (() => {
  let key = null;        // AES key, once unlocked
  let index = null;      // decrypted data/index file: runs, PBs, settings…
  const runFiles = {};   // cache of decrypted per-run files

  // ---------- unlocking (website only) ----------

  const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

  async function decryptFile(path) {
    const res = await fetch(path, { cache: "no-cache" });
    if (!res.ok) throw new Error(`Couldn't download ${path}`);
    const { iv, ct } = await res.json();
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(iv) }, key, b64(ct));
    // Files are gzipped before locking, to keep downloads small on a phone
    const text = await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
    return JSON.parse(text);
  }

  async function deriveKey(password) {
    const { salt, iter } = await (await fetch("data/key.json", { cache: "no-cache" })).json();
    const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt: b64(salt), iterations: iter },
      base, { name: "AES-GCM", length: 256 }, true, ["decrypt"]);
  }

  const REMEMBER = "running-dashboard-key";

  async function unlock(password, remember) {
    key = await deriveKey(password);
    try {
      index = await decryptFile("data/index.json");
    } catch {
      key = null;
      throw new Error("wrong-password");
    }
    try {
      if (remember) localStorage.setItem(REMEMBER, JSON.stringify(await crypto.subtle.exportKey("jwk", key)));
    } catch { /* private browsing – just don't remember */ }
    return true;
  }

  async function unlockRemembered() {
    try {
      const jwk = JSON.parse(localStorage.getItem(REMEMBER) || "null");
      if (!jwk) return false;
      key = await crypto.subtle.importKey("jwk", jwk, { name: "AES-GCM" }, true, ["decrypt"]);
      index = await decryptFile("data/index.json");
      return true;
    } catch {
      forget();
      return false;
    }
  }

  function forget() {
    key = null; index = null;
    try { localStorage.removeItem(REMEMBER); } catch { /* ignore */ }
  }

  async function runFile(id) {
    if (!runFiles[id]) {
      const file = index.files[id];
      if (!file) return null;
      runFiles[id] = await decryptFile(`data/runs/${file}.json`);
    }
    return runFiles[id];
  }

  // ---------- the same questions, answered either way ----------

  return {
    isStatic: IS_STATIC,
    unlock, unlockRemembered, forget,
    isUnlocked: () => !IS_STATIC || !!index,

    async status() {
      if (!IS_STATIC) return getJSON("/api/status");
      return { static: true, configured: true, connected: true, scope_ok: true,
        last_sync: index.last_sync, athlete_name: index.athlete_name, sync: { running: false, phase: "idle" },
        counts: { activities: index.runs.length + (index.activities || []).length, pending: 0 } };
    },

    async runs() {
      if (!IS_STATIC) return getJSON("/api/runs");
      return { runs: index.runs, activities: index.activities || [], pbs: index.pbs, tags: index.tags, new_ids: index.new_ids };
    },

    async run(id) {
      if (!IS_STATIC) {
        const res = await fetch(`/api/runs/${id}`);
        return res.ok ? res.json() : null;
      }
      const run = index.runs.find((r) => r.id === id) || (index.activities || []).find((r) => r.id === id);
      if (!run) return null;
      const f = await runFile(id);
      return { run, detail: (f && f.detail) || {} };
    },

    async compare(ids) {
      if (!IS_STATIC) return getJSON(`/api/compare?ids=${ids.join(",")}`);
      const runs = ids.map((id) => index.runs.find((r) => r.id === id)).filter(Boolean);
      const series = await Promise.all(runs.map(async (r) => ((await runFile(r.id)) || {}).compare || null));
      return { runs, series };
    },

    async maxHr() {
      if (!IS_STATIC) return (await getJSON("/api/settings")).max_hr;
      try {
        const local = Number(localStorage.getItem("running-dashboard-max-hr"));
        if (local) return local;
      } catch { /* ignore */ }
      return index.max_hr || 185;
    },

    async setMaxHr(value) {
      if (!IS_STATIC) {
        const res = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ max_hr: value }) });
        const body = await res.json();
        if (!res.ok) throw new Error(body.error);
        return body.max_hr;
      }
      const n = Math.round(Number(value));
      if (!(n >= 120 && n <= 230)) throw new Error("That doesn't look like a maximum heart rate – try a number between 120 and 230.");
      try { localStorage.setItem("running-dashboard-max-hr", String(n)); } catch { /* ignore */ }
      return n;
    },

    canEditTags: !IS_STATIC,
  };
})();
