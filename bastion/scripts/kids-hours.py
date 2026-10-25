#!/usr/bin/env python3
"""kids-hours.py — each child's internet window, enforced in AdGuard's custom rules.

    kids-hours.py                        cron: make AdGuard match every child's window right now
    kids-hours.py --status               each window's state, overrides, and what AdGuard has
    kids-hours.py --dry-run              show what a run would write, write nothing
    kids-hours.py --open  --child ava   lift Ava's block until her next morning start (a night off)
    kids-hours.py --close --child finn  apply Finn's block until his next morning start (early bedtime)
    kids-hours.py --open|--close [--child X] --until HH:MM   same, but expiring at a time of day
    kids-hours.py --clear [--child X]    drop overrides and go back to the schedule
    kids-hours.py --times                the windows, for the gateway profile and Screen Time

This is layer 2 of docs/plan-kids-hours-and-iot.md. The windows and the device lists live here
and nowhere else. Outside a child's window one rule for that child sits between the marker
lines below, preceded by one rule per device in ALWAYS_OFF; every rule outside the markers is
preserved untouched:

    ! kids-hours: begin (managed by bastion/scripts/kids-hours.py -- do not edit by hand)
    ! always-off: finn-pc -- playroom PC, off at Robby's request 2026-10-12
    ||*^$client='finn-pc',important
    ||*^$client='finn-pc'|'finn-chromebook'|...,important
    ! kids-hours: end

`||*^` is AdGuard's "every domain"; `$client=` limits it to the named persistent clients, so an
adult's device is never touched; `important` makes it outrank plain exception rules such as the
plex.direct allow. Blocked lookups appear in the query log as "Blocked by custom filtering
rule" against the device name, and in Loki with filtered="true".

Overrides are stored with an expiry in OVERRIDES (one JSON object, child -> {"state", "until"})
and honoured by every cron run until they lapse, so a night off survives the 15-minute
self-heal and ends by itself at the next morning start. An ALWAYS_OFF entry has no expiry and
is not an override: it blocks that one device at all hours, through its window, and only
deleting the entry and deploying brings it back. Publishes homelab_kids_window_open{child},
homelab_kids_rule_present{child}, homelab_kids_override{child} and
homelab_kids_device_always_off{device} through the textfile collector.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

# Local time, America/Chicago. "school" nights are Sunday to Thursday, "weekend" Friday and
# Saturday; the start applies every morning. AdGuard persistent-client names carry the child.
CHILDREN = {
    "ava": {                                  # 15
        "school": ("05:30", "23:30"),          # 23:30 every night since 2026-10-25 (was 22:00 / 23:00)
        "weekend": ("05:30", "23:30"),
        "devices": ["ava-phone", "ava-chromebook",
                    # still to identify and add: the iPad mini (ava-ipad-mini, .156)
                    ],
    },
    "finn": {                                 # 13
        "school": ("06:00", "21:30"),
        "weekend": ("06:00", "22:15"),
        "devices": ["finn-pc", "finn-chromebook", "finn-roku", "finn-ipad", "finn-phone"],
    },
}

# Single devices held off the internet at all hours, by AdGuard persistent-client name, for as
# long as the entry is here — this is where an "off until further notice" ask lands. It cuts
# across the windows above: the device stays blocked inside its child's window too, and no
# override lifts it, so --open for that child still leaves this one device dark. The reason is
# part of the rule written into AdGuard, so the query log says why. Removing the entry and
# deploying restores that device's normal window on the next run.
ALWAYS_OFF = {
    # "finn-pc": "playroom PC, off at Robby's request 2026-10-12",   # restored 2026-10-14
}
TZ = ZoneInfo("America/Chicago")

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(HERE), ".env")
TEXTFILE_DIR = "/home/robby/observability/textfile"
LOG = "/home/robby/logs/kids-hours.log"
OVERRIDES = "/home/robby/logs/kids-hours-overrides.json"
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


def next_morning(child, now):
    """The child's next window start: today's if it is still ahead, else tomorrow's."""
    start = hhmm(CHILDREN[child]["school"][0])     # mornings are the same on both kinds
    cand = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    return cand if cand > now else cand + timedelta(days=1)


def load_overrides(now):
    """{child: {"state": "open"|"closed", "until": datetime}} with expired entries dropped."""
    try:
        with open(OVERRIDES) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for child, o in raw.items():
        try:
            until = datetime.fromisoformat(o["until"])
        except (KeyError, ValueError, TypeError):
            continue
        if child in CHILDREN and o.get("state") in ("open", "closed") and until > now:
            out[child] = {"state": o["state"], "until": until}
    return out


def save_overrides(ov):
    data = {c: {"state": o["state"], "until": o["until"].isoformat()} for c, o in ov.items()}
    if DRY:
        print("--- overrides:", json.dumps(data)); return
    os.makedirs(os.path.dirname(OVERRIDES), exist_ok=True)
    with open(OVERRIDES + ".tmp", "w") as fh:
        json.dump(data, fh)
    os.replace(OVERRIDES + ".tmp", OVERRIDES)


def block_rule(child):
    return "||*^$client=" + "|".join(f"'{d}'" for d in CHILDREN[child]["devices"]) + ",important"


def always_off_rules():
    """A comment carrying the reason, then a block rule, per ALWAYS_OFF device. Sorted, so a
    run's output is stable and the no-change check below holds."""
    out = []
    for device, why in sorted(ALWAYS_OFF.items()):
        out.extend([f"! always-off: {device} -- {why}", f"||*^$client='{device}',important"])
    return out


def split_rules(rules):
    """(rules outside the markers, rule lines inside the markers or None if no block)."""
    if BEGIN not in rules:
        return list(rules), None
    b = rules.index(BEGIN)
    e = rules.index(END, b) if END in rules[b:] else len(rules) - 1
    return rules[:b] + rules[e + 1:], rules[b + 1:e]


def write_metrics(state, overrides):
    """state: {child: (is_open, present)}"""
    lines = []
    for child, (is_open, present) in sorted(state.items()):
        lines.append(f'homelab_kids_window_open{{child="{child}"}} {int(is_open)}')
        lines.append(f'homelab_kids_rule_present{{child="{child}"}} {int(present)}')
        lines.append(f'homelab_kids_override{{child="{child}"}} {int(child in overrides)}')
    for device in sorted(ALWAYS_OFF):
        lines.append(f'homelab_kids_device_always_off{{device="{device}"}} 1')
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
        for device, why in sorted(ALWAYS_OFF.items()):
            print(f"Always off, no window: {device} ({why})")
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
    targets = [only] if only else list(CHILDREN)

    now = datetime.now(TZ)
    overrides = load_overrides(now)

    # --open / --close / --clear write an override with an expiry; the schedule then applies
    # it on this run and every cron run until it lapses.
    if "--open" in sys.argv or "--close" in sys.argv:
        state = "open" if "--open" in sys.argv else "closed"
        until_arg = arg_value("--until")
        for child in targets:
            if until_arg:
                u = hhmm(until_arg)
                until = now.replace(hour=u.hour, minute=u.minute, second=0, microsecond=0)
                if until <= now:
                    until += timedelta(days=1)
            else:
                until = next_morning(child, now)
            overrides[child] = {"state": state, "until": until}
            log(f"override: {child} {state} until {until:%a %H:%M}")
        save_overrides(overrides)
    if "--clear" in sys.argv:
        for child in targets:
            if overrides.pop(child, None):
                log(f"override cleared: {child}")
        save_overrides(overrides)

    want_closed, kinds, why = {}, {}, {}
    for child in CHILDREN:
        is_open, kinds[child] = window_open(child, now)
        closed = not is_open
        why[child] = f"{kinds[child]} window"
        if child in overrides:
            closed = overrides[child]["state"] == "closed"
            why[child] = f"override {overrides[child]['state']} until {overrides[child]['until']:%a %H:%M}"
        want_closed[child] = closed

    try:
        current = api("/filtering/status", auth).get("user_rules", [])
    except (urllib.error.URLError, OSError) as e:
        log(f"ERROR: cannot read AdGuard rules: {e}")
        write_metrics({c: (not want_closed[c], False) for c in CHILDREN}, overrides)
        return 1

    outside, inside = split_rules(current)
    # Exact match, so an always-off rule for one of a child's devices is never mistaken for
    # that child's whole-window block — and a changed device list reads as absent and is
    # rewritten.
    present = {c: block_rule(c) in (inside or []) for c in CHILDREN}
    desired_inside = always_off_rules() + [block_rule(c) for c in CHILDREN if want_closed[c]]
    state = {c: (not want_closed[c], present[c]) for c in CHILDREN}

    if "--status" in sys.argv:
        for c in CHILDREN:
            log(f"{now:%a %H:%M} {c}: {'CLOSED' if want_closed[c] else 'OPEN'} ({why[c]}); "
                f"rule {'present' if present[c] else 'absent'}")
        for device, reason in sorted(ALWAYS_OFF.items()):
            in_adguard = f"||*^$client='{device}',important" in (inside or [])
            log(f"{device}: ALWAYS OFF ({reason}); "
                f"rule {'present' if in_adguard else 'absent'}")
        log(f"{len(outside)} other rule(s) untouched")
        write_metrics(state, overrides)
        in_step = (all(present[c] == want_closed[c] for c in CHILDREN)
                   and all(r in (inside or []) for r in always_off_rules()))
        return 0 if in_step else 2

    if (inside or []) == desired_inside:
        write_metrics(state, overrides)
        return 0   # nothing to do; the common case for the 15-minute self-heal

    new_rules = outside + ([BEGIN] + desired_inside + [END] if desired_inside else [])
    changes = ", ".join(f"{c} {'closing' if want_closed[c] else 'opening'} ({why[c]})"
                        for c in CHILDREN if present[c] != want_closed[c])
    # An ALWAYS_OFF entry added or removed moves the rules without moving any window, so say
    # that rather than logging an empty change.
    changes = changes or f"always-off set now [{', '.join(sorted(ALWAYS_OFF)) or 'empty'}]"
    if DRY:
        log(f"would write rules ({changes}):")
        print("\n".join(new_rules))
        write_metrics({c: (not want_closed[c], want_closed[c]) for c in CHILDREN}, overrides)
        return 0
    try:
        api("/filtering/set_rules", auth, {"rules": new_rules})
    except (urllib.error.URLError, OSError) as e:
        log(f"ERROR: cannot write AdGuard rules: {e}")
        write_metrics(state, overrides)
        return 1
    log(f"{changes} at {now:%a %H:%M}")
    write_metrics({c: (not want_closed[c], want_closed[c]) for c in CHILDREN}, overrides)
    return 0


if __name__ == "__main__":
    sys.exit(main())
