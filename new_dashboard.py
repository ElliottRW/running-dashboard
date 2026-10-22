"""Set up a separate dashboard for a friend, in its own folder and GitHub repo.

    python3 new_dashboard.py <name>

For example "python3 new_dashboard.py alex" makes ~/src/running-dashboard-alex and the
GitHub repo running-dashboard-alex, with its website at
https://<your-github-name>.github.io/running-dashboard-alex/

Before you start, your friend needs to:
  1. Create a Strava API app at https://www.strava.com/settings/api
     with "Authorization Callback Domain" set to  localhost
  2. Send you its Client ID and Client Secret
  3. Be ready to log in to Strava (with you, or on a call) when the script asks

The script copies only the code you've committed: none of your runs, your login or your
locked data. If it stops part-way, run it again with the same name and it carries on.
It uses only Python's standard library, so plain python3 is enough.
"""
import getpass
import io
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
CONNECT_TIMEOUT_S = 15 * 60


def say(text):
    print(f"\n==> {text}")


def run(cmd, cwd=None, input_text=None, check=True, capture=False):
    return subprocess.run(cmd, cwd=cwd, input=input_text, text=True, check=check,
                          capture_output=capture)


def ask_yes(question):
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# ---------------------------------------------------------------- checks

def check_tools():
    for tool in ("git", "gh"):
        if not shutil.which(tool):
            sys.exit(f"'{tool}' isn't installed. Install it first (brew install {tool}).")
    if run(["gh", "auth", "status"], check=False, capture=True).returncode != 0:
        sys.exit("The GitHub tool isn't logged in. Run:  gh auth login")
    if run(["git", "status", "--porcelain"], cwd=HERE, capture=True).stdout.strip():
        print("Note: you have uncommitted changes here – only committed code gets copied.")


def github_user():
    return run(["gh", "api", "user", "-q", ".login"], capture=True).stdout.strip()


def free_port(start=5051):
    for port in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    sys.exit("Couldn't find a free port between 5051 and 5100.")


# ---------------------------------------------------------------- steps

def copy_code(dest, name):
    say(f"Copying the code into {dest}")
    # Binary output, so not through run() (which uses text mode)
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=HERE,
                             check=True, capture_output=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        try:
            tar.extractall(dest, filter="data")
        except TypeError:   # older Pythons (like macOS's built-in 3.9) don't have filter=
            tar.extractall(dest)
    # Your locked data and this script stay behind
    shutil.rmtree(os.path.join(dest, "state"), ignore_errors=True)
    if os.path.exists(os.path.join(dest, "new_dashboard.py")):
        os.remove(os.path.join(dest, "new_dashboard.py"))

    # Both websites live on <you>.github.io, so give this one its own "remember me"
    # and max heart rate entries in the browser, or the two sites would share them.
    path = os.path.join(dest, "static", "data.js")
    with open(path) as f:
        js = f.read()
    patched, count = re.subn(r'"running-dashboard-(key|max-hr)"', rf'"running-dashboard-{name}-\1"', js)
    if count != 3:
        sys.exit(f"Expected 3 browser-storage names in static/data.js but found {count} – "
                 "the code has changed; update copy_code() in new_dashboard.py.")
    with open(path, "w") as f:
        f.write(patched)


def write_env(dest):
    say("Your friend's Strava API app")
    client_id = input("Client ID: ").strip()
    while not client_id.isdigit():
        client_id = input("That should be a number. Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret (hidden as you type): ").strip()
    while not re.fullmatch(r"[0-9a-f]{40}", client_secret):
        client_secret = getpass.getpass("That should be 40 letters/numbers. Client Secret: ").strip()

    say("Website password")
    site_password = getpass.getpass("Type one (12+ characters), or press Enter to make a random one: ")
    while site_password and len(site_password) < 12:
        site_password = getpass.getpass("At least 12 characters please, or Enter for a random one: ")
    if not site_password:
        site_password = secrets.token_urlsafe(18)

    port = free_port()
    path = os.path.join(dest, ".env")
    with open(path, "w") as f:
        f.write("# Made by new_dashboard.py – never committed to git\n"
                f"STRAVA_CLIENT_ID={client_id}\n"
                f"STRAVA_CLIENT_SECRET={client_secret}\n"
                f"SITE_PASSWORD={site_password}\n"
                f"PORT={port}\n")
    os.chmod(path, 0o600)


def read_env(dest):
    values = {}
    with open(os.path.join(dest, ".env")) as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                values[k.strip()] = v.strip()
    return values


def make_venv(dest):
    py = os.path.join(dest, ".venv", "bin", "python")
    if os.path.exists(py):
        return py
    say("Installing Python packages (a minute or so)")
    run([sys.executable, "-m", "venv", ".venv"], cwd=dest)
    run([py, "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=dest)
    return py


def connected_athlete(dest):
    path = os.path.join(dest, "data", "running.db")
    if not os.path.exists(path):
        return None
    try:
        with sqlite3.connect(path, timeout=5) as conn:
            row = conn.execute("SELECT athlete_name, refresh_token FROM auth WHERE id = 1").fetchone()
    except sqlite3.Error:
        return None
    if not (row and row[1]):
        return None
    return row[0] or "(no name)"


def connect_strava(dest, py, port):
    athlete = connected_athlete(dest)
    if athlete:
        print(f"\nStrava is already connected as {athlete}.")
        return
    url = f"http://localhost:{port}/auth/login"
    say("Connecting your friend's Strava account")
    print("Your browser is probably logged in to YOUR Strava, which would connect the wrong account.\n"
          "So open this link in a private/incognito window and let your friend log in there,\n"
          "leaving \"View data about your private activities\" ticked:\n\n"
          f"    {url}\n")
    server = subprocess.Popen([py, "app.py"], cwd=dest,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2)
        if ask_yes("Open it in your normal browser instead?"):
            webbrowser.open(url)
        print("Waiting for the Strava login to finish…")
        deadline = time.time() + CONNECT_TIMEOUT_S
        while not (athlete := connected_athlete(dest)):
            if server.poll() is not None:
                sys.exit("The dashboard stopped unexpectedly. Try:  "
                         f"cd {dest} && .venv/bin/python app.py")
            if time.time() > deadline:
                sys.exit("Gave up waiting for the Strava login. Run this script again to retry.")
            time.sleep(2)
    finally:
        server.terminate()
        server.wait()
    print(f"\nConnected as: {athlete}")
    if not ask_yes("Is that your friend (not you)?"):
        os.remove(os.path.join(dest, "data", "running.db"))
        sys.exit("Removed that login. Run the script again and use a private window.")


def create_repo(dest, repo):
    if not os.path.exists(os.path.join(dest, ".git")):
        say("Making the first commit")
        run(["git", "init", "-q", "-b", "main"], cwd=dest)
        run(["git", "add", "-A"], cwd=dest)
        run(["git", "commit", "-q", "-m", "Running dashboard"], cwd=dest)
    if run(["gh", "repo", "view", repo], check=False, capture=True).returncode == 0:
        print(f"\nGitHub repo {repo} already exists.")
        return
    say(f"Creating the GitHub repo {repo}")
    run(["gh", "repo", "create", repo, "--public", "--source", ".", "--push"], cwd=dest)


def set_secrets(repo, env):
    say("Saving the Strava keys and website password as GitHub secrets")
    for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "SITE_PASSWORD"):
        # Passed on stdin so they never appear in the list of running commands
        run(["gh", "secret", "set", name, "--repo", repo], input_text=env[name], capture=True)


def enable_pages(repo):
    say("Turning on GitHub Pages")
    result = run(["gh", "api", "-X", "POST", f"repos/{repo}/pages", "-f", "build_type=workflow"],
                 check=False, capture=True)
    if result.returncode != 0:
        if "already" in (result.stdout + result.stderr).lower():
            run(["gh", "api", "-X", "PUT", f"repos/{repo}/pages", "-f", "build_type=workflow"],
                capture=True)
        else:
            sys.exit("Couldn't turn on GitHub Pages:\n" + result.stderr +
                     f"\nDo it by hand: https://github.com/{repo}/settings/pages → Source: GitHub Actions,"
                     " then run this script again.")


def add_collaborator(repo):
    say("Their Update button")
    print("The website's Update button only works for people who can run the repo's workflow.\n"
          "Adding your friend as a collaborator lets them – but also lets them change the code.")
    user = input("Their GitHub username (or Enter to skip): ").strip().lstrip("@")
    if user:
        run(["gh", "api", "-X", "PUT", f"repos/{repo}/collaborators/{user}",
             "-f", "permission=push"], capture=True)
        print(f"Invited {user} – they need to accept the email from GitHub.")


# ---------------------------------------------------------------- main

def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", sys.argv[1]):
        sys.exit(__doc__ + "\nThe name must be lower-case letters, numbers or dashes.")
    name = sys.argv[1]
    check_tools()
    owner = github_user()
    repo = f"{owner}/running-dashboard-{name}"
    dest = os.path.join(os.path.dirname(HERE), f"running-dashboard-{name}")
    site = f"https://{owner.lower()}.github.io/running-dashboard-{name}/"

    if not os.path.exists(dest):
        os.makedirs(dest)
        copy_code(dest, name)
    if not os.path.exists(os.path.join(dest, ".env")):
        write_env(dest)
    env = read_env(dest)
    py = make_venv(dest)
    connect_strava(dest, py, env["PORT"])
    create_repo(dest, repo)
    set_secrets(repo, env)
    enable_pages(repo)

    say("Locking and uploading the starting data")
    run([py, "cloud.py", "push"], cwd=dest)
    say("Starting the first update on GitHub")
    run(["gh", "workflow", "run", "sync.yml", "--repo", repo])
    add_collaborator(repo)

    print(f"""
All done. In a few minutes (longer if they have lots of runs) the site will be at:

    {site}

Website password: {env['SITE_PASSWORD']}
Send your friend the link and password separately, e.g. the password by text.

Watch the first update:  https://github.com/{repo}/actions
Their files are in:      {dest}
If their Strava login ever expires, GitHub emails YOU – see README section 2,
running the commands from {dest}.
""")


if __name__ == "__main__":
    main()
