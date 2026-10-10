#!/usr/bin/env python3
"""
plex-resume-restart.py — Restart Plex once after cinema wakes from suspend.

Plex keeps running across a suspend, and afterwards its cached plex.tv access tokens are
stale: the owner's own token is refused ("authenticating user as guest", 401 on
/media/providers), so every client shows "no libraries" or "not authorized" until Plex is
restarted. It recurred every few weeks, each time after a wake. See README (cinema).

How a resume is noticed without root: CLOCK_BOOTTIME keeps counting while the machine is
suspended and CLOCK_MONOTONIC does not, so their difference only ever grows, by exactly the
time spent asleep. Cron runs this every minute; if the difference has grown since the last run,
the machine has been suspended in between. cron does not replay missed jobs, so the first run
after a wake is the one that sees it.

The first run (no state file) only records the baseline.
"""
import subprocess
import sys
import time
import urllib.request

STATE = "/home/robby/.plex-suspended-seconds"
MIN_SLEEP = 30      # ignore drift smaller than this
WAIT_NET = 120      # how long to wait for plex.tv to be reachable after a wake


def slept_seconds() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()


def read_state():
    try:
        with open(STATE) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


def network_up() -> bool:
    try:
        urllib.request.urlopen("https://plex.tv/api/v2/ping", timeout=5)
        return True
    except Exception:
        return False


def main() -> int:
    now = slept_seconds()
    before = read_state()
    with open(STATE, "w") as f:
        f.write(f"{now:.0f}\n")
    if before is None or now - before < MIN_SLEEP:
        return 0

    # Restarting before the network is back would just reproduce the stale state.
    deadline = time.time() + WAIT_NET
    while not network_up():
        if time.time() > deadline:
            print("plex.tv unreachable after wake; restarting anyway", file=sys.stderr)
            break
        time.sleep(5)

    print(f"resumed after {now - before:.0f}s asleep; restarting plex")
    return subprocess.run(["docker", "restart", "plex"], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
