#!/usr/bin/env python3
"""kids-hours.py — the children's internet window, enforced in AdGuard's custom rules.

    kids-hours.py               cron: make AdGuard match the window right now (idempotent)
    kids-hours.py --status      print whether the window is open, and what AdGuard has
    kids-hours.py --dry-run     show what a run would write, write nothing
    kids-hours.py --open        remove the block regardless of the time (rollback / a night off)
    kids-hours.py --close       apply the block regardless of the time (an early bedtime)
    kids-hours.py --times       print the window for copying into the gateway profile and Screen Time

This is layer 2 of docs/plan-kids-hours-and-iot.md. The window and the device list live here and
nowhere else. Outside the window one rule is present in AdGuard's user rules, between the two
marker lines below, and every rule outside the markers is preserved untouched:

    ! kids-hours: begin (managed by bastion/scripts/kids-hours.py -- do not edit by hand)
    ||*^$client='kid-pc'|'kid-chromebook'|...
    ! kids-hours: end

`||*^` is AdGuard's "every domain"; `$client=` limits it to the named persistent clients, so an
adult's device is never touched. Blocked lookups appear in the query log as "Blocked by custom
filtering rule" against the child's device name, and in Loki with filtered="true".

--open and --close are one-shots: the next cron run puts the rule back where the clock says it
should be, so a night off lasts until 07:00 at most and an early bedtime until the next boundary.
Publishes homelab_kids_window_open{} (1 open, 0 closed) and homelab_kids_rule_present{} through
the textfile collector, so a rule stuck in the wrong state is one alert away.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, time
from zoneinfo import ZoneInfo

WINDOW = {                        # local time, America/Chicago
    "school": ("07:00", "20:00"),   # Sun–Thu nights end at 20:00
    "weekend": ("07:00", "21:30"),  # Fri and Sat nights end at 21:30
}
KIDS = [                          # AdGuard persistent-client names, one per device
    "kid-pc", "kid-chromebook", "kid-roku", "kid-ipad", "kid-phone-finn", "kid-phone-ava",
    # still to identify and add: the iPad mini (kid-ipad-mini, .156)
]
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


def window_open(now):
    """Open between start and end on the day; the end depends on which night is *starting*.
    Friday and Saturday nights get the later end; Sunday to Thursday nights the earlier one."""
    kind = "weekend" if now.weekday() in (4, 5) else "school"   # Mon=0 … Fri=4, Sat=5
    start, end = (hhmm(x) for x in WINDOW[kind])
    return start <= now.time() < end, kind


def block_rule():
    return "||*^$client=" + "|".join(f"'{k}'" for k in KIDS)


def split_rules(rules):
    """(rules outside the markers, rule lines inside the markers or None if no block)."""
    if BEGIN not in rules:
        return list(rules), None
    b = rules.index(BEGIN)
    e = rules.index(END, b) if END in rules[b:] else len(rules) - 1
    return rules[:b] + rules[e + 1:], rules[b + 1:e]


def write_metrics(is_open, present):
    lines = [f"homelab_kids_window_open {int(is_open)}", f"homelab_kids_rule_present {int(present)}"]
    if DRY:
        print("--- kids-hours.prom"); print("\n".join(lines)); return
    if not os.path.isdir(TEXTFILE_DIR):
        return
    path = os.path.join(TEXTFILE_DIR, "kids-hours.prom")
    with open(path + ".tmp", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)


def main():
    if "--times" in sys.argv:
        print("Sun–Thu nights: internet %s–%s" % WINDOW["school"])
        print("Fri and Sat nights: internet %s–%s" % WINDOW["weekend"])
        print("Devices:", ", ".join(KIDS))
        return 0

    env = load_env()
    user, pw = env.get("ADGUARD_USER"), env.get("ADGUARD_PASSWORD")
    if not (user and pw):
        log("ERROR: ADGUARD_USER/ADGUARD_PASSWORD missing from .env")
        return 1
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()

    now = datetime.now(TZ)
    is_open, kind = window_open(now)
    want_closed = not is_open
    if "--open" in sys.argv:
        want_closed = False
    if "--close" in sys.argv:
        want_closed = True

    try:
        current = api("/filtering/status", auth).get("user_rules", [])
    except (urllib.error.URLError, OSError) as e:
        log(f"ERROR: cannot read AdGuard rules: {e}")
        write_metrics(is_open, False)
        return 1

    outside, inside = split_rules(current)
    present = inside is not None and any(l.startswith("||*^$client=") for l in inside)
    desired_inside = [block_rule()] if want_closed else []

    if "--status" in sys.argv:
        log(f"{now:%a %H:%M} {kind} window {'OPEN' if is_open else 'CLOSED'}; "
            f"rule {'present' if present else 'absent'}; {len(outside)} other rule(s)")
        write_metrics(is_open, present)
        return 0 if present == want_closed else 2

    if inside == desired_inside or (inside is None and not desired_inside):
        write_metrics(is_open, present)
        return 0   # nothing to do; the common case for the 15-minute self-heal

    new_rules = outside + ([BEGIN] + desired_inside + [END] if desired_inside else [])
    action = "closing (block on)" if want_closed else "opening (block off)"
    if DRY:
        log(f"would write rules, {action}:")
        print("\n".join(new_rules))
        write_metrics(is_open, want_closed)
        return 0
    try:
        api("/filtering/set_rules", auth, {"rules": new_rules})
    except (urllib.error.URLError, OSError) as e:
        log(f"ERROR: cannot write AdGuard rules: {e}")
        write_metrics(is_open, present)
        return 1
    log(f"{action} for {len(KIDS)} devices at {now:%a %H:%M} ({kind} window)")
    write_metrics(is_open, want_closed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
