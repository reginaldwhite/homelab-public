#!/usr/bin/env python3
"""
kids-internet.py — the children's internet windows, from the agent.

Usage:
  python3 kids-internet.py status              # each child's window: open/closed, block on/off
  python3 kids-internet.py open  [ava|finn]  # no block until that child's next morning (night off)
  python3 kids-internet.py close [ava|finn]  # block until the next morning (early bedtime)
  python3 kids-internet.py clear [ava|finn]  # drop the override, back to the schedule

Same shape as plex-power.py: the container cannot reach AdGuard, so every action is one SSH
call to bastion with a fixed verb, and bastion's forced command (openclaw-cmd) runs
scripts/kids-hours.py with the matching flags. Nothing typed here becomes an argument on the
far side -- the verb is the whole command. open/close write an override on bastion that
lasts until that child's next morning start and then lapses; clear ends it early.
"""

import subprocess
import sys

BASTION_IP = "192.168.0.10"
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "UserKnownHostsFile=/home/node/.openclaw/known_hosts",
    "-o", "ConnectTimeout=10",
    "-i", "/home/node/.ssh/openclaw_ed25519",
]
CHILDREN = ("ava", "finn")


def run_verb(verb: str) -> int:
    result = subprocess.run(["ssh", *SSH_OPTS, f"robby@{BASTION_IP}", verb],
                            capture_output=True, text=True)
    out = (result.stdout + result.stderr).strip()
    print(out or f"(no output from bastion for {verb})")
    if result.returncode not in (0, 2):
        print(f"bastion refused or failed (exit {result.returncode})")
    return 0 if result.returncode in (0, 2) else result.returncode


def main(argv):
    if not argv or argv[0] not in ("status", "open", "close", "clear"):
        print(__doc__); return 1
    action = argv[0]
    child = argv[1] if len(argv) > 1 else None
    if child is not None and child not in CHILDREN:
        print(f"unknown child {child!r}; known: {', '.join(CHILDREN)}"); return 1
    if action == "status":
        verb = "kids-status"
    else:
        verb = f"kids-{action}" + (f"-{child}" if child else "")
    return run_verb(verb)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
