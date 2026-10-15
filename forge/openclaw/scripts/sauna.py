#!/usr/bin/env python3
"""
sauna.py — the HUUM sauna, from the agent.

Usage:
  python3 sauna.py status                        heater state and any pending start
  python3 sauna.py schedule HH:MM [TARGET] [MIN] heat at HH:MM (24 h clock), default 93 °C, 180 min
  python3 sauna.py start [TARGET]                heat now
  python3 sauna.py stop                          stop heating, drop any pending start
  python3 sauna.py cancel                        drop the pending start only
  python3 sauna.py next                          the heat-up experiment: the next run's setup
  python3 sauna.py runs                          the runs so far
  python3 sauna.py phase [begin MODE | decide MODE adapts|flat]   where it stands
  python3 sauna.py label clear | mode=M bench=N ceiling=N          what was actually set

Same shape as plex-power.py: one SSH call to bastion with a verb; bastion's forced command
validates the arguments against a fixed pattern and runs scripts/sauna.py there. The
schedule is kept on bastion and fired by its cron, so the controller cannot lose it.
"""

import re
import subprocess
import sys

BASTION_IP = "192.168.0.10"
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "UserKnownHostsFile=/home/node/.openclaw/known_hosts",
    "-o", "ConnectTimeout=10",
    "-i", "/home/node/.ssh/openclaw_ed25519",
]
TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
INT = re.compile(r"^[0-9]{2,3}$")
MODE = re.compile(r"^(simple|learning)$")
LABEL = re.compile(r"^(mode=(simple|learning)|bench=([0-9]|[1-9][0-9]|100)|ceiling=([0-9]|[1-9][0-9]|100))$")


def run_verb(verb: str) -> int:
    r = subprocess.run(["ssh", *SSH_OPTS, f"robby@{BASTION_IP}", verb], capture_output=True, text=True)
    print((r.stdout + r.stderr).strip() or f"(no output from bastion for {verb})")
    return r.returncode


def main(argv):
    if not argv or argv[0] not in ("status", "schedule", "start", "stop", "cancel", "next", "runs", "phase", "label"):
        print(__doc__); return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "phase":
        ok = (not rest or (len(rest) == 2 and rest[0] == "begin" and MODE.match(rest[1]))
              or (len(rest) == 3 and rest[0] == "decide" and MODE.match(rest[1]) and rest[2] in ("adapts", "flat")))
        if not ok:
            print("phase takes nothing, 'begin simple|learning', or 'decide simple|learning adapts|flat'"); return 1
        return run_verb("sauna-phase" + (" " + " ".join(rest) if rest else ""))
    if cmd == "label":
        if not (rest == ["clear"] or (1 <= len(rest) <= 3 and all(LABEL.match(x) for x in rest))):
            print("label takes 'clear' or up to three of mode=simple|learning bench=0-100 ceiling=0-100"); return 1
        return run_verb("sauna-label " + " ".join(rest))
    if cmd == "schedule":
        if not rest or not TIME.match(rest[0]) or not all(INT.match(x) for x in rest[1:3]) or len(rest) > 3:
            print("schedule wants HH:MM (24 h), optionally a target in °C and minutes"); return 1
        return run_verb("sauna-schedule " + " ".join(rest))
    if cmd == "start":
        if len(rest) > 1 or (rest and not INT.match(rest[0])):
            print("start takes at most a target in °C"); return 1
        return run_verb("sauna-start" + (f" {rest[0]}" if rest else ""))
    if rest:
        print(f"{cmd} takes no arguments"); return 1
    return run_verb(f"sauna-{cmd}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
