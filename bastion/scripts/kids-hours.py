#!/usr/bin/env python3
"""kids-hours.py — each child's internet window, enforced in AdGuard's custom rules.

    kids-hours.py               cron: make AdGuard match every child's window right now (idempotent)
    kids-hours.py --status      print each window's state and what AdGuard has
    kids-hours.py --dry-run     show what a run would write, write nothing
    kids-hours.py --open        remove every block regardless of the time (rollback / a night off)
    kids-hours.py --close       apply every block regardless of the time (an early bedtime)
    kids-hours.py --times       print the windows for copying into the gateway profile and Screen Time
    --open / --close accept --child ava|finn to act on one child only.

This is layer 2 of docs/plan-kids-hours-and-iot.md. The windows and the device lists live here
and nowhere else. Outside a child's window one rule for that child sits between the marker
lines below; every rule outside the markers is preserved untouched:

    ! kids-hours: begin (managed by bastion/scripts/kids-hours.py -- do not edit by hand)
    ||*^$client='finn-pc'|'finn-chromebook'|...,important
    ! kids-hours: end

`||*^` is AdGuard's "every domain"; `$client=` limits it to the named persistent clients, so an
adult's device is never touched; `important` makes it outrank plain exception rules such as the
plex.direct allow, so Plex is off too. Blocked lookups appear in the query log as "Blocked by
custom filtering rule" against the device name, and in Loki with filtered="true".

--open and --close are one-shots: the next cron run puts the rules back where the clock says
they should be, so a night off lasts until the morning at most and an early bedtime until the
next boundary. Publishes homelab_kids_window_open{child} (1 open, 0 closed) and
homelab_kids_rule_present{child} through the textfile collector, so a rule stuck in the wrong
state is one alert away.
"""

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, time
from zoneinfo import ZoneInfo

# Local time, America/Chicago. "school" nights are Sunday to Thursday, "weekend" Friday and
# Saturday; the start applies every morning. AdGuard persistent-client names carry the child.
CHILDREN = {
    "ava": {                                  # 15
        "school": ("05:30", "22:00"),
        "weekend": ("05:30", "23:00"),
        "devices": ["ava-phone",
                    # still to identify and add: the iPad mini (ava-ipad-mini, .156)
                    ],
    },
    "finn": {                                 # 13
        "school": ("06:00", "21:30"),
        "weekend": ("06:00", "22:15"),
        "devices": ["finn-pc", "finn-chromebook", "finn-roku", "finn-ipad", "finn-phone"],
    },
}
TZ = ZoneInfo("America/Chicago")

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(HERE), ".env")
TEXTFILE_DIR = "/home/robby/observability/textfile"
LOG = "/home/robby/logs/kids-hours.log"
API = "http://127.0.0.1:8888/control"
BEGIN = "! kids-hours: begin (managed by bastion/scripts/kids-hours.py -- do not edit by hand)"
END = "! kids-hours: end"

DRY = "--dry-run" in sys.argv


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now().astimezone().isoformat()} [kids-hours] {msg}\n")
    if DRY or "--status" in sys.argv:
        print(msg)


def load_env():
    env = {}
    try:
        with open(ENV_FILE) as fh:
            for line in fh:
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def api(path, auth, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method="POST" if data else "GET",
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        return json.loads(raw) if raw.strip() else {}


def hhmm(s):
    h, m = s.split(":")
    return time(int(h), int(m))


def window_open(child, now):
    """Open between start and end on the day; the end depends on which night is *starting*.
    Friday and Saturday nights get the later end; Sunday to Thursday nights the earlier one."""
    kind = "weekend" if now.weekday() in (4, 5) else "school"   # Mon=0 … Fri=4, Sat=5
    start, end = (hhmm(x) for x in CHILDREN[child][kind])
    return start <= now.time() < end, kind


def block_rule(child):
    return "||*^$client=" + "|".join(f"'{d}'" for d in CHILDREN[child]["devices"]) + ",important"


def rule_child(line):
    """Which child a managed block line belongs to, by its first device name; None if not ours."""
    m = re.match(r"\|\|\*\^\$client='([a-z]+)-", line)
    return m.group(1) if m else None


def split_rules(rules):
    """(rules outside the markers, rule lines inside the markers or None if no block)."""
    if BEGIN not in rules:
        return list(rules), None
    b = rules.index(BEGIN)
    e = rules.index(END, b) if END in rules[b:] else len(rules) - 1
    return rules[:b] + rules[e + 1:], rules[b + 1:e]


def write_metrics(state):
    """state: {child: (is_open, present)}"""
    lines = []
    for child, (is_open, present) in sorted(state.items()):
        lines.append(f'homelab_kids_window_open{{child="{child}"}} {int(is_open)}')
        lines.append(f'homelab_kids_rule_present{{child="{child}"}} {int(present)}')
    if DRY:
        print("--- kids-hours.prom"); print("\n".join(lines)); return
    if not os.path.isdir(TEXTFILE_DIR):
        return
    path = os.path.join(TEXTFILE_DIR, "kids-hours.prom")
    with open(path + ".tmp", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)


def arg_value(flag):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    return None


def main():
    if "--times" in sys.argv:
        for child, c in CHILDREN.items():
            print(f"{child.capitalize()}: Sun–Thu nights {c['school'][0]}–{c['school'][1]}, "
                  f"Fri–Sat nights {c['weekend'][0]}–{c['weekend'][1]}; devices: {', '.join(c['devices'])}")
        return 0

    env = load_env()
    user, pw = env.get("ADGUARD_USER"), env.get("ADGUARD_PASSWORD")
    if not (user and pw):
        log("ERROR: ADGUARD_USER/ADGUARD_PASSWORD missing from .env")
        return 1
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()

    only = arg_value("--child")
    if only and only not in CHILDREN:
        log(f"ERROR: unknown child {only!r}; known: {', '.join(CHILDREN)}")
        return 1

    now = datetime.now(TZ)
    want_closed, kinds = {}, {}
    for child in CHILDREN:
        is_open, kinds[child] = window_open(child, now)
        closed = not is_open
        if "--open" in sys.argv and (only in (None, child)):
            closed = False
        if "--close" in sys.argv and (only in (None, child)):
            closed = True
        want_closed[child] = closed

    try:
        current = api("/filtering/status", auth).get("user_rules", [])
    except (urllib.error.URLError, OSError) as e:
        log(f"ERROR: cannot read AdGuard rules: {e}")
        write_metrics({c: (not want_closed[c], False) for c in CHILDREN})
        return 1

    outside, inside = split_rules(current)
    present = {c: False for c in CHILDREN}
    for line in inside or []:
        c = rule_child(line)
        if c in present:
            present[c] = True
    desired_inside = [block_rule(c) for c in CHILDREN if want_closed[c]]
    state = {c: (not want_closed[c], present[c]) for c in CHILDREN}

    if "--status" in sys.argv:
        for c in CHILDREN:
            log(f"{now:%a %H:%M} {c}: {kinds[c]} window {'CLOSED' if want_closed[c] else 'OPEN'}; "
                f"rule {'present' if present[c] else 'absent'}")
        log(f"{len(outside)} other rule(s) untouched")
        write_metrics(state)
        return 0 if all(present[c] == want_closed[c] for c in CHILDREN) else 2

    if (inside or []) == desired_inside:
        write_metrics(state)
        return 0   # nothing to do; the common case for the 15-minute self-heal

    new_rules = outside + ([BEGIN] + desired_inside + [END] if desired_inside else [])
    changes = ", ".join(f"{c} {'closing' if want_closed[c] else 'opening'}"
                        for c in CHILDREN if present[c] != want_closed[c])
    if DRY:
        log(f"would write rules ({changes}):")
        print("\n".join(new_rules))
        write_metrics({c: (not want_closed[c], want_closed[c]) for c in CHILDREN})
        return 0
    try:
        api("/filtering/set_rules", auth, {"rules": new_rules})
    except (urllib.error.URLError, OSError) as e:
        log(f"ERROR: cannot write AdGuard rules: {e}")
        write_metrics(state)
        return 1
    log(f"{changes} at {now:%a %H:%M}")
    write_metrics({c: (not want_closed[c], want_closed[c]) for c in CHILDREN})
    return 0


if __name__ == "__main__":
    sys.exit(main())
