#!/usr/bin/env python3
"""
idle-suspend.py — put cinema to sleep after an hour of nobody using Plex, overnight.

Why: cinema only ever sleeps when someone says "sleep plex". After a power outage it boots by
itself and then runs for days (29 hours after the 2026-10-10 outage before anyone noticed).
Robby asked (2026-10-11) for it to suspend on its own after midnight once it is unused.

How: cron runs this every 15 minutes between 00:00 and 05:45. "In use" means any of:
  - a Plex playback session (/status/sessions),
  - a Plex background activity such as a library scan or analysis (/activities),
  - someone logged in over SSH or at the console (`who`).
The time of the last run that saw use is kept in a state file; a resume from suspend or a
fresh boot also counts as use, so a machine woken at 01:00 gets a full idle hour before this
puts it back. After IDLE_MINUTES without use it runs `sudo systemctl suspend`, which robby's
sudoers allows without a password (the same path plex-power.py's "sleep" uses over SSH).

The Plex token is read from Plex's own Preferences.xml through `docker exec` each run (robby
is in the docker group) and never stored. If the token or the API cannot be read, the script
logs and exits without suspending: an unknown state is not an idle one.

Resume detection is the same trick as plex-resume-restart.py: CLOCK_BOOTTIME keeps counting
through a suspend and CLOCK_MONOTONIC does not, so their difference grows by exactly the time
asleep. cron does not replay runs missed while suspended, so the first run after a wake is the
one that notices.

    idle-suspend.py            # the cron entry point
    idle-suspend.py --dry-run  # say what it would do, change nothing
"""
import json
import subprocess
import sys
import time
import urllib.request

STATE = "/home/robby/.idle-suspend-state"
IDLE_MINUTES = 60
PLEX = "http://127.0.0.1:32400"
PREFS = "/config/Library/Application Support/Plex Media Server/Preferences.xml"
DRY = "--dry-run" in sys.argv


def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} [idle-suspend] {msg}", flush=True)


def slept_seconds():
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()


def plex_token():
    out = subprocess.run(["docker", "exec", "plex", "sh", "-c",
                          f'grep -o \'PlexOnlineToken="[^"]*"\' "{PREFS}" | cut -d\\" -f2'],
                         capture_output=True, text=True, timeout=20)
    tok = out.stdout.strip()
    return tok if out.returncode == 0 and tok else None


def plex_count(path, token):
    """The MediaContainer size of a Plex endpoint, or None if it cannot be read."""
    req = urllib.request.Request(f"{PLEX}{path}", headers={"X-Plex-Token": token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(json.load(resp)["MediaContainer"].get("size", 0))
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown", handled by the caller
        log(f"could not read {path}: {exc}")
        return None


def users_logged_in():
    out = subprocess.run(["who"], capture_output=True, text=True)
    return len([l for l in out.stdout.splitlines() if l.strip()])


def read_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(state):
    if DRY:
        return
    with open(STATE, "w") as fh:
        json.dump(state, fh)


def main():
    now = time.time()
    state = read_state()
    slept = slept_seconds()

    token = plex_token()
    if not token:
        log("cannot read Plex's token; not suspending on unknown state")
        return 1
    sessions = plex_count("/status/sessions", token)
    activities = plex_count("/activities", token)
    if sessions is None or activities is None:
        return 1
    users = users_logged_in()

    reasons = []
    if sessions:
        reasons.append(f"{sessions} playback session(s)")
    if activities:
        reasons.append(f"{activities} Plex activity(ies) such as a scan")
    if users:
        reasons.append(f"{users} user(s) logged in")
    if not state or abs(slept - state.get("slept", -1)) > 30:
        reasons.append("resumed or booted since the last run")
    if time.monotonic() < IDLE_MINUTES * 60:
        reasons.append(f"up less than {IDLE_MINUTES} min")

    if reasons:
        state.update({"last_active": now, "slept": slept})
        write_state(state)
        log("in use: " + "; ".join(reasons))
        return 0

    idle_min = (now - state.get("last_active", now)) / 60
    state["slept"] = slept
    write_state(state)
    if idle_min < IDLE_MINUTES:
        log(f"idle for {idle_min:.0f} min; suspending at {IDLE_MINUTES}")
        return 0
    log(f"idle for {idle_min:.0f} min with nobody using Plex; suspending" + (" (dry run)" if DRY else ""))
    if not DRY:
        subprocess.run(["sudo", "-n", "/bin/systemctl", "suspend"], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
