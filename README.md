# My Runs – personal Strava running dashboard

Your Strava runs, worked out properly from the GPS track: personal bests, weekly totals, km
splits, route maps, plain-English notes on each run, and a side-by-side compare page.

It comes in two versions that share the same code:

| | **Mac version** | **Website** (GitHub Pages) |
|---|---|---|
| Where | `http://localhost:5050` on your Mac | `https://<your-github-name>.github.io/running-dashboard/` |
| Syncing | When you open it / press *Sync now* | Automatically every morning, or press **Update** |
| Login | Not needed – only your Mac can open it | Password screen |
| Tags | Add and edit | Read-only |
| Route | Full route | Full route |

**Built with:** Python + Flask (a small, popular web server), SQLite (a database that's a single
file), and plain HTML/JavaScript with the Leaflet map library. No build tools needed.

---

## 1. Starting the Mac version

Open Terminal and run:

```bash
cd ~/src/running-dashboard && .venv/bin/python app.py
```

Then open <http://localhost:5050>. Leave that Terminal window open while you use it;
press **Ctrl+C** in it to stop.

### First-time setup (already done – here for reference)

1. On <https://www.strava.com/settings/api> set **Authorization Callback Domain** to `localhost`.
2. `cp .env.example .env`, then put your Client ID and Client Secret into `.env`.
   `.env` is in `.gitignore`, so it's never uploaded.
3. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. Start the app and click **Connect to Strava**.

---

## 2. Reconnecting Strava if the login stops working

Signs: the Mac version shows **"Connect to Strava"** again, or GitHub emails you that
**"Sync runs" failed** with *"Your Strava login has expired or was revoked"*.

This happens if you remove the app's access on Strava, change your Strava password, or regenerate
the Client Secret.

1. If you regenerated the **Client Secret** on Strava, put the new one in `.env`.
2. Start the Mac version and click **Connect to Strava**. Leave "private activities" ticked.
3. If you use the website, send the new login up to GitHub:
   ```bash
   .venv/bin/python cloud.py push
   ```
   and if you changed the Client Secret, also:
   ```bash
   gh secret set -f .env
   ```
4. On GitHub, open **Actions → Sync runs → Run workflow** to check it works.

---

## 3. Backing up your data

Everything lives in **one file: `data/running.db`** (your runs, tags, settings and Strava login).

- **Quick backup** (safe even while the app is running):
  ```bash
  sqlite3 data/running.db ".backup '$HOME/Documents/running-backup.db'"
  ```
- **To restore**, stop the app and copy the backup back to `data/running.db`.
- **Time Machine** backs up the whole project folder automatically if you use it.
- **If you've set up the website**, GitHub also keeps a password-locked copy in
  `state/running.db.locked` – with every past version in its history. To get it back onto
  your Mac: `.venv/bin/python cloud.py pull`.

The backup contains your Strava login, so keep it somewhere private.

---

## 4. The website (GitHub Pages)

### How it works

- Every morning GitHub runs [`.github/workflows/sync.yml`](.github/workflows/sync.yml). It
  unlocks your saved data, fetches any new runs from Strava (a handful of requests – nowhere near
  the limits), rebuilds the site and publishes it.
- The **Update** button on the website opens that workflow on GitHub. Tap **Run workflow**, wait
  about 2 minutes, then reload the site. (It can't start the update directly, because that would
  mean putting a GitHub password inside the website.)
- **GitHub Pages sites are public**, so everything is **locked with your password** (AES-256
  encryption, done in your browser). Without the password, the files are unreadable – including
  your routes, which start from home, so keep the password long and private.
- Optional **privacy zone**: to hide the route near every start and finish, change
  `PRIVACY_RADIUS_M: "0"` in `.github/workflows/sync.yml` to e.g. `"250"` (metres). It's off by default.
- Your Strava keys and password live in GitHub's **secrets** store, never in the code.

### One-time setup

1. **Pick a website password** – long and unique (for example four random words). Because the
   locked files are public, a short password could be guessed by a computer. Add it to `.env`:
   ```
   SITE_PASSWORD=your long password here
   ```
2. **Create the GitHub repository and upload the code** (it must be public for free GitHub Pages;
   only code and locked data go into it):
   ```bash
   git add -A && git commit -m "Running dashboard"
   ```
   ```bash
   gh repo create running-dashboard --public --source . --push
   ```
3. **Give GitHub your secrets** (reads `.env` on your Mac – you never paste them anywhere):
   ```bash
   gh secret set -f .env
   ```
4. **Turn on GitHub Pages**: on GitHub open the repo → **Settings → Pages** → under *Source*
   choose **GitHub Actions**.
5. **Upload your data** (locks it with your password first):
   ```bash
   .venv/bin/python cloud.py push
   ```
6. **Run the first update**: repo → **Actions → Sync runs → Run workflow**. After ~2 minutes
   your site is at `https://<your-github-name>.github.io/running-dashboard/`.
7. On your phone, open it, enter the password, tick **Remember on this device**, and use
   *Share → Add to Home Screen* for an app-like icon.

### Everyday use

- Just open the website. It updates itself each morning.
- After a run, press **Update → Run workflow** if you don't want to wait.
- **Tagging runs** happens on the Mac: `cloud.py pull` → tag in the Mac version → `cloud.py push`.
  ```bash
  .venv/bin/python cloud.py pull
  ```
  ```bash
  .venv/bin/python cloud.py push
  ```
- Once the website is set up, the Mac version **doesn't sync by itself** (two copies refreshing the
  same Strava login can log each other out). Use `cloud.py pull` to get the latest runs.
- GitHub pauses scheduled jobs if a repo has had **no commits for 60 days** (e.g. a long break
  from running). If that happens, GitHub emails you – just click to re-enable it.

### Changing the website password

Change `SITE_PASSWORD` in `.env`, then run `gh secret set -f .env` and `.venv/bin/python cloud.py push`,
then **Run workflow**. Devices that "remembered" the old password will ask for the new one.

---

## 5. Giving a friend their own dashboard

This setup is for **one Strava account**. A website on GitHub Pages has no server to keep other
people's Strava logins safe, and Strava also limits new API apps to the owner's account unless
Strava approves an increase (your API settings page shows this).

The good option is for each friend to have **their own copy**:

1. They make a GitHub account and click **Fork** on your repository (this copies the code – not
   your data, which stays locked with your password; they should delete the `state/` folder in their copy).
2. They create their own Strava API app at <https://www.strava.com/settings/api> with callback
   domain `localhost`.
3. They follow **First-time setup** (section 1) and **The website** (section 4) with their own
   keys and password.

---

## 6. How the numbers are worked out

- **Distance** is measured along the GPS track (not Strava's summary), skipping impossible GPS jumps.
  Treadmill runs fall back to the watch's distance, then Strava's.
- **Pace** uses moving time (stops don't count) and is smoothed over ~30 seconds in charts.
- **Personal bests** check every possible stretch of 1 / 5 / 10 km inside every GPS run.
- **Climb** is from lightly smoothed height data, ignoring wobbles under 1 m.
- **"How it went"** notes are simple rules in [`analysis.py`](analysis.py) (`summarise`): first km
  vs the rest (±4%), first half vs second half (±2–3%), and heart rate rising ≥3 bpm while pace held.
- **Hard runs**: average heart rate above 85% of the max heart rate set on the Overview page.
- **Duplicates** (same start within 2 minutes, distance within 5%) are shown greyed out and left
  out of totals and PBs.

## 7. What's where

| File | What it does |
|---|---|
| `app.py` | The Mac web server and Strava login |
| `strava.py` | Talking to Strava politely (token refresh, rate limits) |
| `sync.py` | Fetching runs and their detail, resuming where it stopped |
| `db.py` | The SQLite database |
| `analysis.py` | All the maths: distance, splits, PBs, notes, compare data |
| `cloud.py` | The website: locking, privacy zones, building, daily sync |
| `static/` | The pages you see (HTML, CSS, JavaScript) |
| `.github/workflows/sync.yml` | GitHub's daily job |
| `state/` | Your locked data for the website (safe to be public) |
| `data/` | Your unlocked data on the Mac – **never uploaded** |
