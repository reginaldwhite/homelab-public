#!/usr/bin/env python3
"""sauna.py — start, stop and schedule HUUM sauna sessions from bastion.

    sauna.py status                              what the heater is doing, and any pending start
    sauna.py schedule HH:MM [TARGET] [MINUTES]   heat at HH:MM today (tomorrow if already past)
    sauna.py start [TARGET]                      heat now
    sauna.py stop                                stop heating now, and drop any pending start
    sauna.py cancel                              drop the pending start, leave the heater alone
    sauna.py --tick                              cron, every minute: fire a due start, stop a due end

Why this exists: the HUUM controller forgets a start that was armed on its own panel whenever
it reconnects to its cloud (docs/plan-kids-hours-and-iot.md), and the app is awkward. HUUM's
API has no "start later" of its own -- only start-at-a-target and stop -- so the schedule is
kept here and bastion sends the start at the minute. Defaults: 93 °C, 180 minutes (the
controller's own maximum; a shorter session is stopped by the tick).

Safety: the API refuses to start with the door open, and so does this script (it checks
first and reports instead of retrying blindly; the tick retries a due start for ten minutes
then gives up with an error event). Credentials are HUUM_USERNAME / HUUM_PASSWORD in ../.env.
Every outcome is a `sauna/scheduled-start` or `sauna/control` event and a log line.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
API = "https://api.huum.eu/action/home/"
DEFAULT_TARGET = 93
DEFAULT_MINUTES = 180
TARGET_RANGE = (40, 110)
MINUTES_RANGE = (30, 180)
RETRY_MINUTES = 10

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(HERE), ".env")
STATE = "/home/robby/logs/sauna-schedule.json"
LOG = "/home/robby/logs/sauna.log"
TEXTFILE_DIR = "/home/robby/observability/textfile"
EVENT_SH = os.path.join(HERE, "event.sh")
CODES = {230: "offline", 231: "heating", 232: "idle", 233: "locked", 400: "emergency stop"}


def log(msg, echo=True):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now(TZ).isoformat()} [sauna] {msg}\n")
    if echo:
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


def auth_header():
    env = load_env()
    u, p = env.get("HUUM_USERNAME"), env.get("HUUM_PASSWORD")
    if not (u and p):
        raise SystemExit("HUUM_USERNAME/HUUM_PASSWORD missing from .env")
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode(),
            "Accept": "application/json", "Content-Type": "application/json"}


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method="POST" if data is not None else "GET",
                                 headers=auth_header())
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    return json.loads(raw) if raw.strip() else {}


def record_event(name, status, detail):
    if not os.path.exists(EVENT_SH):
        return
    os.system(f'"{EVENT_SH}" sauna {name} {status} --detail \'{json.dumps(detail)}\' >/dev/null 2>&1')


def load_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + ".tmp", "w") as fh:
        json.dump(st, fh, indent=1)
    os.replace(STATE + ".tmp", STATE)
    write_metric(st)


def write_metric(st):
    if not os.path.isdir(TEXTFILE_DIR):
        return
    p = st.get("pending") or {}
    ts = int(datetime.fromisoformat(p["start_at"]).timestamp()) if p else 0
    path = os.path.join(TEXTFILE_DIR, "sauna-schedule.prom")
    with open(path + ".tmp", "w") as fh:
        fh.write(f"huum_scheduled_start_timestamp {ts}\n")
    os.replace(path + ".tmp", path)


def describe(s):
    code = int(s.get("statusCode") or 0)
    out = f"{CODES.get(code, code)}, {s.get('temperature')} °C"
    if code == 231:
        out += f", target {s.get('targetTemperature')} °C"
        end = int(s.get("endDate") or 0)
        if end:
            out += f", ends {datetime.fromtimestamp(end, TZ):%H:%M}"
    if not s.get("door", True):
        out += ", DOOR OPEN"
    return out


def start_heater(target, why):
    s = api("status")
    if not s.get("door", True):
        return False, "door is open; not starting"
    code = int(s.get("statusCode") or 0)
    if code == 230:
        return False, "controller is offline"
    if code == 400:
        return False, "controller reports emergency stop"
    if code == 231 and int(float(s.get("targetTemperature") or 0)) == target:
        return True, f"already heating to {target} °C"
    r = api("start", {"targetTemperature": int(target)})
    code = int(r.get("statusCode") or 0)
    if code == 231:
        record_event("control", "ok", {"action": "start", "target_c": target, "why": why})
        return True, f"heating to {target} °C" + (f", ends {datetime.fromtimestamp(int(r['endDate']), TZ):%H:%M}" if r.get("endDate") else "")
    return False, f"start was not accepted (status {CODES.get(code, code)})"


def stop_heater(why):
    r = api("stop")
    record_event("control", "ok", {"action": "stop", "why": why})
    return f"stopped ({describe(r)})"


def parse_args(argv):
    target, minutes = DEFAULT_TARGET, DEFAULT_MINUTES
    if len(argv) > 0:
        target = int(argv[0])
    if len(argv) > 1:
        minutes = int(argv[1])
    if not TARGET_RANGE[0] <= target <= TARGET_RANGE[1]:
        raise SystemExit(f"target must be {TARGET_RANGE[0]}–{TARGET_RANGE[1]} °C")
    if not MINUTES_RANGE[0] <= minutes <= MINUTES_RANGE[1]:
        raise SystemExit(f"minutes must be {MINUTES_RANGE[0]}–{MINUTES_RANGE[1]}")
    return target, minutes


def cmd_status(st):
    try:
        s = api("status")
        print(f"sauna: {describe(s)}")
    except (urllib.error.URLError, OSError) as e:
        print(f"sauna: cloud unreachable ({e})")
    p = st.get("pending")
    if p:
        print(f"pending: start {datetime.fromisoformat(p['start_at']):%a %H:%M} at {p['target']} °C for {p['minutes']} min")
    e = st.get("stop_at")
    if e:
        print(f"stop due: {datetime.fromisoformat(e):%a %H:%M}")
    if not p and not e:
        print("pending: nothing scheduled")


def cmd_schedule(st, argv):
    if not argv:
        raise SystemExit("usage: sauna.py schedule HH:MM [TARGET] [MINUTES]")
    hh, mm = argv[0].split(":")
    now = datetime.now(TZ)
    start = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if start <= now:
        start += timedelta(days=1)
    target, minutes = parse_args(argv[1:])
    st["pending"] = {"start_at": start.isoformat(), "target": target, "minutes": minutes,
                     "created": now.isoformat(), "attempts": 0}
    save_state(st)
    log(f"scheduled: {start:%a %H:%M} at {target} °C for {minutes} min")


def tick(st):
    now = datetime.now(TZ)
    p = st.get("pending")
    if p and datetime.fromisoformat(p["start_at"]) <= now:
        p["attempts"] = p.get("attempts", 0) + 1
        try:
            ok, msg = start_heater(p["target"], "schedule")
        except (urllib.error.URLError, OSError) as e:
            ok, msg = False, f"cloud unreachable ({e})"
        if ok:
            log(f"scheduled start fired ({msg})", echo=False)
            record_event("scheduled-start", "ok", {"target_c": p["target"], "minutes": p["minutes"], "attempts": p["attempts"]})
            if p["minutes"] < DEFAULT_MINUTES:
                st["stop_at"] = (now + timedelta(minutes=p["minutes"])).isoformat()
            st["pending"] = None
            save_state(st)
        elif now - datetime.fromisoformat(p["start_at"]) >= timedelta(minutes=RETRY_MINUTES):
            log(f"scheduled start GAVE UP after {p['attempts']} attempts: {msg}", echo=False)
            record_event("scheduled-start", "error", {"target_c": p["target"], "reason": msg, "attempts": p["attempts"]})
            st["pending"] = None
            save_state(st)
        else:
            log(f"scheduled start not yet possible (attempt {p['attempts']}): {msg}", echo=False)
            save_state(st)
    e = st.get("stop_at")
    if e and datetime.fromisoformat(e) <= now:
        try:
            msg = stop_heater("schedule")
            log(f"scheduled stop: {msg}", echo=False)
        except (urllib.error.URLError, OSError) as exc:
            log(f"scheduled stop failed: {exc}", echo=False)
        st["stop_at"] = None
        save_state(st)


def main(argv):
    st = load_state()
    if not argv:
        print(__doc__); return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "--tick":
        tick(st); return 0
    if cmd == "status":
        cmd_status(st); return 0
    if cmd == "schedule":
        cmd_schedule(st, rest); cmd_status(st); return 0
    if cmd == "cancel":
        had = st.get("pending"); st["pending"] = None; save_state(st)
        log("cancelled the pending start" if had else "nothing was pending"); return 0
    if cmd == "start":
        target, _ = parse_args(rest[:1])
        ok, msg = start_heater(target, "manual")
        log(f"start: {msg}"); return 0 if ok else 1
    if cmd == "stop":
        st["pending"] = None; st["stop_at"] = None; save_state(st)
        log(f"stop: {stop_heater('manual')}"); return 0
    print(__doc__); return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except urllib.error.HTTPError as e:
        log(f"ERROR: HUUM API {e.code} on {e.url.rsplit('/', 1)[-1]}"); sys.exit(1)
    except (urllib.error.URLError, OSError) as e:
        log(f"ERROR: {e}"); sys.exit(1)
