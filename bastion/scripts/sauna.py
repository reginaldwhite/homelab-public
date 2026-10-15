#!/usr/bin/env python3
"""sauna.py — start, stop and schedule HUUM sauna sessions from bastion.

    sauna.py status                              what the heater is doing, and any pending start
    sauna.py schedule HH:MM [TARGET] [MINUTES]   heat at HH:MM today (tomorrow if already past)
    sauna.py start [TARGET]                      heat now
    sauna.py stop                                stop heating now, and drop any pending start
    sauna.py cancel                              drop the pending start, leave the heater alone
    sauna.py --tick                              cron, every minute: fire a due start, stop a due end

The heat-up experiment (docs/plan-sauna-heatup.md) -- which heating mode, then which vents:
    sauna.py phase                               where the experiment stands
    sauna.py phase begin simple|learning         start phase A; the argument is the mode set today
    sauna.py phase decide simple|learning adapts|flat
                                                 after phase A: the winner, and whether it adapts
    sauna.py next                                the next run's setup; leaves it as the pending label
    sauna.py label mode=learning bench=0 ceiling=10
                                                 what was actually set, if different (or 'label clear')
    sauna.py runs                                the runs so far, newest first
The pending label is attached by iot-poll.py to the next session that starts heating.

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
import random
import subprocess
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
PLAN = "/home/robby/logs/sauna-plan.json"
IOT_STATE = "/home/robby/logs/iot-state.json"     # iot-poll.py's; says whether a run is open now
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


def api(path, body=None, post=False):
    """status is a GET; start and stop are POSTs (stop with an empty body -- a GET on it is a 404,
    which is how the first live stop on 2026-10-09 failed)."""
    data = json.dumps(body).encode() if body is not None else (b"" if post else None)
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
    r = api("stop", post=True)
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


# --- the heat-up experiment (docs/plan-sauna-heatup.md) ---------------------------------
# Phase A compares the two heating modes with the vents where they are today (bench closed,
# ceiling 10 %): three runs in the mode set today, then six in the other, judged on its last
# three. Phase B tests the vents at the ends of their sliders in the winning mode: two
# replicates of the four cells in a seeded random order -- or, if the winner adapts across
# sessions, three consecutive runs per cell with the first excluded as the adaptation run.
MODES = ("simple", "learning")
A_VENTS = {"bench_pct": 0, "ceiling_pct": 10}
A1_RUNS, A2_RUNS = 3, 6
B_CELLS = [(0, 0), (0, 100), (100, 0), (100, 100)]
B_REPLICATES, B_BLOCK = 2, 3
SEED = 20261015


def db(sql, **vars):
    """One statement in the events database via `docker exec … psql`, values as psql variables
    (never spliced into SQL). Rows as lists of strings; None if the database is not reachable."""
    cmd = ["docker", "exec", "-i", "events-db", "psql", "-q", "-At", "-U", "homelab", "-d", "homelab",
           "-v", "ON_ERROR_STOP=1"]
    for k, v in vars.items():
        cmd += ["-v", f"{k}={'' if v is None else v}"]
    try:
        r = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        print(f"database: {r.stderr.strip()[:160]}")
        return None
    return [line.split("|") for line in r.stdout.splitlines() if line]


def load_plan():
    try:
        with open(PLAN) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_plan(plan):
    os.makedirs(os.path.dirname(PLAN), exist_ok=True)
    with open(PLAN + ".tmp", "w") as fh:
        json.dump(plan, fh, indent=1)
    os.replace(PLAN + ".tmp", PLAN)


def open_run_id():
    """The run iot-poll.py has open right now, if the heater is heating."""
    try:
        with open(IOT_STATE) as fh:
            run = (json.load(fh).get("huum") or {}).get("run") or {}
        return run.get("id")
    except (OSError, ValueError):
        return None


def phase_counts():
    """Runs that count towards each phase's plan: valid ones, plus finished adaptation runs."""
    rows = db("select phase, count(*) from sauna_runs where phase is not null and ended_at is not null "
              "and (valid or adaptation_run) group by phase")
    return {ph: int(n) for ph, n in (rows or [])}


def b_order(plan):
    """Phase B's run list, fixed by the seed so it is the same every time it is asked for."""
    rng = random.Random(plan.get("seed", SEED))
    order = []
    if plan.get("adapts"):
        cells = list(B_CELLS); rng.shuffle(cells)
        for b, c in cells:
            for i in range(B_BLOCK):
                order.append({"bench_pct": b, "ceiling_pct": c, "adaptation_run": i == 0})
    else:
        for _ in range(B_REPLICATES):
            cells = list(B_CELLS); rng.shuffle(cells)
            order += [{"bench_pct": b, "ceiling_pct": c, "adaptation_run": False} for b, c in cells]
    return order


def setup_line(label):
    s = f"mode {label['mode'].upper()}, bench {label['bench_pct']} %, ceiling {label['ceiling_pct']} %"
    if label.get("adaptation_run"):
        s += " (adaptation run: recorded, not scored)"
    return s


def next_run(plan):
    """Work out the next run from the plan and what the database says is done; return the label
    to leave pending, or None with a message when there is nothing to set up."""
    phase = plan.get("phase")
    if not phase:
        return None, "no experiment in progress: 'phase begin simple' or 'phase begin learning' first (the mode set today)"
    done = phase_counts()
    if phase == "A":
        other = MODES[1] if plan["current_mode"] == MODES[0] else MODES[0]
        a1, a2 = done.get("A1", 0), done.get("A2", 0)
        if a1 < A1_RUNS:
            return {"phase": "A1", "plan_seq": a1 + 1, "mode": plan["current_mode"], **A_VENTS}, \
                f"phase A1 run {a1 + 1} of {A1_RUNS}"
        note = f"phase A2 run {a2 + 1} of {A2_RUNS}"
        if a2 >= A2_RUNS:
            note = f"phase A is complete ({A2_RUNS} runs of {other}); this is an extra A2 run {a2 + 1} -- decide with 'phase decide' when ready"
        return {"phase": "A2", "plan_seq": a2 + 1, "mode": other, **A_VENTS}, note
    if phase == "B":
        order = b_order(plan)
        b = done.get("B", 0)
        if b >= len(order):
            return None, f"phase B is complete ({len(order)} runs); read the effects table on the Sauna heat-up dashboard"
        return {"phase": "B", "plan_seq": b + 1, "mode": plan["winner"], **order[b]}, f"phase B run {b + 1} of {len(order)}"
    return None, f"phase {phase}: nothing planned"


def cmd_next(plan):
    if open_run_id():
        print("the sauna is heating now; the next setup is for the session after this one")
    label, note = next_run(plan)
    if not label:
        print(note); return 1
    plan["pending"] = label
    save_plan(plan)
    print(f"{note}: {setup_line(label)} -- set the panel and the sliders, then start as usual")
    return 0


def cmd_label(plan, argv):
    if argv == ["clear"]:
        plan["pending"] = None; save_plan(plan)
        print("pending label cleared"); return 0
    fields = {}
    for a in argv:
        k, _, v = a.partition("=")
        if k == "mode" and v in MODES:
            fields["mode"] = v
        elif k in ("bench", "ceiling") and v.isdigit() and 0 <= int(v) <= 100:
            fields[k + "_pct"] = int(v)
        else:
            raise SystemExit("label wants mode=simple|learning, bench=0-100, ceiling=0-100 (any of them), or 'clear'")
    if not fields:
        raise SystemExit("label wants mode=simple|learning, bench=0-100, ceiling=0-100 (any of them), or 'clear'")
    run_id = open_run_id()
    if run_id:
        sets = ", ".join(f"{k} = :'{k}'::{'text' if k == 'mode' else 'int'}" for k in fields)
        db(f"update sauna_runs set {sets} where id = :'id'::bigint", id=run_id, **fields)
        print(f"run {run_id} (heating now) labelled: " + ", ".join(f"{k} {v}" for k, v in fields.items()))
        return 0
    pending = dict(plan.get("pending") or {})
    pending.update(fields)
    if not all(k in pending for k in ("mode", "bench_pct", "ceiling_pct")):
        print("note: the label is incomplete until mode, bench and ceiling are all set")
    plan["pending"] = pending
    save_plan(plan)
    print("pending for the next session: " + ", ".join(f"{k} {v}" for k, v in pending.items()))
    return 0


def cmd_runs():
    rows = db("""select id, to_char(started_at at time zone 'America/Chicago', 'MM-DD HH24:MI'),
                        coalesce(phase, '-'), coalesce(plan_seq::text, '-'), coalesce(mode, '-'),
                        coalesce(bench_pct::text, '-'), coalesce(ceiling_pct::text, '-'),
                        coalesce(start_c::text, '-'), coalesce(t_80_min::text, '-'), coalesce(t_target_min::text, '-'),
                        coalesce(overshoot_c::text, '-'),
                        case when ended_at is null then 'heating' when valid then 'ok' else 'invalid: ' || coalesce(invalid_reason, '') end
                 from sauna_runs order by started_at desc limit 20""")
    if rows is None:
        return 1
    if not rows:
        print("no runs recorded yet"); return 0
    print("id    started      phase seq mode      bench ceil  start  t80   t_tgt  over  status")
    for r in rows:
        print(f"{r[0]:<5} {r[1]:<12} {r[2]:<5} {r[3]:<3} {r[4]:<9} {r[5]:<5} {r[6]:<5} {r[7]:<6} {r[8]:<5} {r[9]:<6} {r[10]:<5} {r[11]}")
    return 0


def cmd_phase(plan, argv):
    if argv[:1] == ["begin"] and len(argv) == 2 and argv[1] in MODES:
        if plan.get("phase"):
            raise SystemExit(f"the experiment is already in phase {plan['phase']}; nothing changed")
        plan.update({"phase": "A", "current_mode": argv[1], "winner": None, "adapts": None, "seed": SEED,
                     "began": datetime.now(TZ).isoformat(), "pending": None})
        save_plan(plan)
        log(f"experiment: phase A begun, mode set today is {argv[1]}")
    elif argv[:1] == ["decide"] and len(argv) == 3 and argv[1] in MODES and argv[2] in ("adapts", "flat"):
        if plan.get("phase") != "A":
            raise SystemExit(f"'phase decide' is for the end of phase A; the experiment is in phase {plan.get('phase')}")
        plan.update({"phase": "B", "winner": argv[1], "adapts": argv[2] == "adapts",
                     "decided": datetime.now(TZ).isoformat(), "pending": None})
        save_plan(plan)
        log(f"experiment: phase A decided -- {argv[1]} ({argv[2]}); phase B has {len(b_order(plan))} runs")
    elif argv:
        raise SystemExit("usage: sauna.py phase | phase begin simple|learning | phase decide simple|learning adapts|flat")
    phase = plan.get("phase")
    if not phase:
        print("experiment: not begun ('phase begin simple' or 'phase begin learning' -- the mode set today)"); return 0
    done = phase_counts()
    if phase == "A":
        other = MODES[1] if plan["current_mode"] == MODES[0] else MODES[0]
        print(f"phase A: {done.get('A1', 0)}/{A1_RUNS} runs in {plan['current_mode']} (today's mode), "
              f"{done.get('A2', 0)}/{A2_RUNS} in {other}; vents bench 0 %, ceiling 10 %")
    elif phase == "B":
        print(f"phase B: {done.get('B', 0)}/{len(b_order(plan))} runs in {plan['winner']} "
              f"({'adapts: blocks of three, first excluded' if plan['adapts'] else 'two replicates, random order'})")
    p = plan.get("pending")
    print("pending label: " + (setup_line(p) if p and all(k in p for k in ("mode", "bench_pct", "ceiling_pct"))
                              else (json.dumps(p) if p else "none -- 'next' sets one")))
    rid = open_run_id()
    if rid:
        print(f"run {rid} is heating now")
    return 0


def main(argv):
    st = load_state()
    if not argv:
        print(__doc__); return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "--tick":
        tick(st); return 0
    if cmd == "next":
        return cmd_next(load_plan())
    if cmd == "label":
        return cmd_label(load_plan(), rest)
    if cmd == "runs":
        return cmd_runs()
    if cmd == "phase":
        return cmd_phase(load_plan(), rest)
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
