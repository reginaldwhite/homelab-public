#!/usr/bin/env python3
"""iot-poll.py — read the sauna and the sprinklers, publish gauges, record sessions and runs.

    iot-poll.py --poll-huum    every minute from cron: the sauna's gauges, plus the heat-up
                               experiment's run row and one-minute samples (docs/plan-sauna-heatup.md)
    iot-poll.py --poll-rachio  every 5 minutes from cron: the sprinklers
    iot-poll.py --poll         both at once (by hand)
    iot-poll.py --dry-run      print what --poll would publish and record; write nothing

Both devices are read from their vendors' clouds (neither exposes a local API), with the
credentials in ../.env: HUUM_USERNAME/HUUM_PASSWORD (the app login, HTTP basic auth against
api.huum.eu) and RACHIO_API_KEY (from the app's Profile screen, bearer token).

What comes out:

  gauges  ~/observability/textfile/iot-huum.prom and iot-rachio.prom, read by node exporter
          -- the sauna's status code, temperature, target, door, session end; the sprinkler
          controller's online state, rain delay, whether a zone is running. These are the
          "right now" view that Grafana graphs.
  runs    a `sauna_runs` row per heating session with its setup (the label sauna.py left
          pending) and, when it ends, the heat-up responses -- minutes to 80 and to target,
          overshoot, the curve's tail slope, how steadily it holds -- plus a `sauna_samples`
          row a minute while heating. The raw status payload is logged once per session, so
          a field the API only shows while heating is not missed.
  events  a `sauna/session` row when a heating session ends (started_at, minutes, peak_c);
          a `sauna/schedule-lost` row when an armed start disappears without heating;
          and one `sprinkler/<what>` row per finished Rachio event (zone completed, schedule
          completed or skipped, rain delay on/off, controller offline/online), keyed by
          Rachio's own event id so re-reading a day never duplicates.

Rachio allows 3,500 requests a day; this uses three per poll, under 900.

The script reads only. Turning the sauna on from anywhere is a separate decision.
See docs/plan-kids-hours-and-iot.md (Part 2) and docs/plan-observability.md.
"""

import base64
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(HERE), ".env")
TEXTFILE_DIR = "/home/robby/observability/textfile"
STATE_FILE = "/home/robby/logs/iot-state.json"
LOG = "/home/robby/logs/iot-poll.log"
EVENT_SH = os.path.join(HERE, "event.sh")
PLAN_FILE = "/home/robby/logs/sauna-plan.json"     # sauna.py's: the pending label is read here
DB_CONTAINER = "events-db"
EXPERIMENT_TARGET = 91                              # 196 F; a run at any other target is invalid

HUUM_STATUS = "https://api.huum.eu/action/home/status"
RACHIO = "https://api.rach.io/1/public"

HUUM_CODES = {230: "offline", 231: "heating", 232: "idle", 233: "locked", 400: "emergency_stop"}
# Rachio event subtypes worth a row, and the status each gets.
RACHIO_EVENTS = {
    "ZONE_COMPLETED": "ok", "ZONE_STOPPED": "ok",
    "SCHEDULE_COMPLETED": "ok", "SCHEDULE_STOPPED": "ok",
    "WEATHER_INTELLIGENCE_SKIP": "skipped", "WEATHER_INTELLIGENCE_CLIMATE_SKIP": "skipped",
    "WEATHER_INTELLIGENCE_FREEZE": "skipped", "RAIN_DELAY_ON": "skipped", "RAIN_DELAY_OFF": "ok",
    "OFFLINE": "error", "ONLINE": "ok", "COLD_REBOOT": "ok",
}

DRY = "--dry-run" in sys.argv


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now().astimezone().isoformat()} [iot-poll] {msg}\n")
    if DRY:
        print(msg)


def load_env():
    env = {}
    try:
        with open(ENV_FILE) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def get_json(url, headers, timeout=20):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def write_prom(stem, lines):
    """Atomic write for the textfile collector; skipped on a dry run or without the directory."""
    if DRY:
        print(f"--- {stem}.prom")
        print("\n".join(lines))
        return
    if not os.path.isdir(TEXTFILE_DIR):
        return
    path = os.path.join(TEXTFILE_DIR, f"{stem}.prom")
    with open(path + ".tmp", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)


def record_event(kind, name, status, started=None, detail=None, source_id=None):
    cmd = [EVENT_SH, kind, name, status, "--host", "bastion"]
    if started:
        cmd += ["--started", started]
    if detail is not None:
        cmd += ["--detail", json.dumps(detail)]
    if source_id:
        cmd += ["--source-id", source_id]
    if DRY:
        print("--- event:", " ".join(cmd[1:]))
        return
    subprocess.run(cmd, check=False)


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state, keys):
    """Write back only the sections this invocation polled, merged over the file as it is now.
    The HUUM poll runs every minute and the Rachio poll every five; when both start in the
    same second, whichever finished last used to overwrite the other's state with the copy it
    had loaded -- which is how the first experiment run (2026-10-15 18:50) was opened twice."""
    if DRY:
        return
    current = load_state()
    for k in keys:
        current[k] = state.get(k)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE + ".tmp", "w") as fh:
        json.dump(current, fh, indent=1)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)


def iso(ts_seconds):
    return datetime.fromtimestamp(int(ts_seconds), tz=timezone.utc).astimezone().isoformat()


def db(sql, **vars):
    """One statement in the events database through `docker exec … psql`, every value a psql
    variable (the event.sh pattern: nothing is spliced into SQL). Returns the rows as lists of
    strings, [] for none, or None when the database is absent or the statement failed --
    logged, never fatal, like event.sh."""
    if DRY:
        print("--- sql:", " ".join(sql.split())[:150], {k: v for k, v in vars.items()})
        return []
    cmd = ["docker", "exec", "-i", DB_CONTAINER, "psql", "-q", "-At", "-U", "homelab", "-d", "homelab",
           "-v", "ON_ERROR_STOP=1"]
    for k, v in vars.items():
        cmd += ["-v", f"{k}={'' if v is None else v}"]
    try:
        r = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"db: {exc}")
        return None
    if r.returncode != 0:
        log(f"db: {r.stderr.strip()[:200]}")
        return None
    return [line.split("|") for line in r.stdout.splitlines() if line]


# --- the heat-up experiment (docs/plan-sauna-heatup.md) ---------------------------------
def read_plan():
    try:
        with open(PLAN_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def consume_label(plan, run_id):
    plan["pending"] = None
    plan["last_run_id"] = run_id
    if DRY:
        return
    with open(PLAN_FILE + ".tmp", "w") as fh:
        json.dump(plan, fh, indent=1)
    os.replace(PLAN_FILE + ".tmp", PLAN_FILE)


def run_start(h, raw, now, temp, target):
    """First heating sample of a session: open the run with whatever label sauna.py left
    pending (an unlabelled session still gets a row and its numbers). The run clock starts at
    this sample, not at HUUM's startDate, so every response is measured the same way."""
    plan = read_plan()
    label = (plan or {}).get("pending") or {}
    if label and temp >= 40:
        # A warm start is never an experiment run (a second session of the evening, or the
        # cloud still describing a session that has ended): leave the label for the next cold one.
        log(f"huum: warm start ({temp:g}°C); the pending label stays for the next cold start")
        label = {}
    rows = db("""insert into sauna_runs (started_at, target_c, mode, bench_pct, ceiling_pct, phase, plan_seq,
                                         adaptation_run, start_c)
                 values (to_timestamp(:'ts'::bigint), nullif(:'tg', '')::numeric, nullif(:'m', ''), nullif(:'b', '')::int,
                         nullif(:'c', '')::int, nullif(:'ph', ''), nullif(:'sq', '')::int, :'ad'::boolean, :'t0'::numeric)
                 returning id""",
              ts=now, tg=target, m=label.get("mode"), b=label.get("bench_pct"), c=label.get("ceiling_pct"),
              ph=label.get("phase"), sq=label.get("plan_seq"), ad="true" if label.get("adaptation_run") else "false",
              t0=temp)
    run_id = int(rows[0][0]) if rows else None
    h["run"] = {"id": run_id, "started_ts": now, "target": target, "door_opened_ts": None}
    if run_id and plan is not None and label:
        consume_label(plan, run_id)
    log(f"huum: run {run_id} started, {json.dumps(label) if label else 'unlabelled'}, start {temp:g}°C; "
        f"raw status {json.dumps(raw, sort_keys=True)}")
    if run_id is None:
        log("huum: run row could not be written; this session's samples will not be recorded")


def run_sample(run, now, temp, target, door_closed, code):
    if not run.get("id"):
        return
    db("""insert into sauna_samples (run_id, ts, temp_c, target_c, door_closed, status_code)
          values (:'r'::bigint, to_timestamp(:'ts'::bigint), :'t'::numeric, nullif(:'tg', '')::numeric, :'d'::boolean, :'c'::int)
          on conflict do nothing""",
       r=run["id"], ts=now, t=temp, tg=target or "", d="true" if door_closed else "false", c=code)
    if not door_closed and not run.get("door_opened_ts"):
        run["door_opened_ts"] = now
        db("update sauna_runs set door_opened_at = to_timestamp(:'ts'::bigint) where id = :'r'::bigint", ts=now, r=run["id"])
        log(f"huum: run {run['id']}: door opened")


def heatup_metrics(samples, started_ts, target):
    """samples: (ts, temp, door_closed) in time order. Minutes are from the first heating sample."""
    def first_at(threshold):
        return next((ts for ts, t, _ in samples if t is not None and t >= threshold), None)

    def mins(ts):
        return None if ts is None else round((ts - started_ts) / 60, 1)

    t80 = first_at(80)
    ttgt = first_at(target) if target else None
    temps = [t for _, t, _ in samples if t is not None]
    m = {"t_80_min": mins(t80), "t_target_min": mins(ttgt), "peak_c": max(temps) if temps else None,
         "overshoot_c": None, "tail_slope": None, "settle_sd": None, "target_ts": ttgt}
    if ttgt is None:
        return m
    after = [t for ts, t, _ in samples if t is not None and ttgt <= ts <= ttgt + 1800]
    if after:
        m["overshoot_c"] = round(max(after) - target, 1)
    # The curve's knee: the rate over the last 10 degrees before target against the rate over
    # 50..70, as a ratio. 1 is a straight line; well under 1 means the heater eased off.
    t_tail, t50, t70 = first_at(target - 10), first_at(50), first_at(70)
    if t_tail and ttgt > t_tail and t50 and t70 and t70 > t50:
        m["tail_slope"] = round((10 / (ttgt - t_tail)) / (20 / (t70 - t50)), 2)
    settle = [t for ts, t, d in samples if t is not None and d and ttgt + 1800 <= ts <= ttgt + 3600]
    if len(settle) >= 10:
        m["settle_sd"] = round(statistics.pstdev(settle), 2)
    return m


def run_finish(run, now, end_state):
    """Heating stopped (or the controller vanished): compute the responses from the samples and
    decide whether the run can be used by the fit. Invalid runs keep their numbers and say why."""
    if not run.get("id"):
        return
    rows = db("select extract(epoch from ts)::bigint, temp_c, door_closed from sauna_samples "
              "where run_id = :'r'::bigint order by ts", r=run["id"])
    samples = [(int(ts), float(t) if t else None, d == "t") for ts, t, d in (rows or [])]
    target = run.get("target") or 0
    m = heatup_metrics(samples, run["started_ts"], target)
    label = db("select mode, bench_pct, ceiling_pct, start_c from sauna_runs where id = :'r'::bigint", r=run["id"])
    mode, bench, ceiling, start_c = (label[0] if label else ("", "", "", ""))
    start_c = float(start_c) if start_c else None

    reasons = []
    if not (mode and bench != "" and ceiling != ""):
        reasons.append("no label")
    if start_c is None or start_c >= 40:
        reasons.append(f"warm start ({start_c:g}°C)" if start_c is not None else "no start temperature")
    if int(target) != EXPERIMENT_TARGET:
        reasons.append(f"target {target:g}, not {EXPERIMENT_TARGET}")
    if m["t_target_min"] is None:
        reasons.append("controller went offline before target" if end_state == "offline" else "never reached target")
    elif run.get("door_opened_ts") and run["door_opened_ts"] < m["target_ts"]:
        reasons.append("door opened before target")
    valid = not reasons
    db("""update sauna_runs
          set ended_at = to_timestamp(:'ts'::bigint), end_state = :'es',
              t_80_min = nullif(:'t80', '')::numeric, t_target_min = nullif(:'ttg', '')::numeric,
              overshoot_c = nullif(:'ov', '')::numeric, tail_slope = nullif(:'tl', '')::numeric,
              settle_sd = nullif(:'sd', '')::numeric, peak_c = nullif(:'pk', '')::numeric,
              valid = :'v'::boolean, invalid_reason = nullif(:'ir', '')
          where id = :'r'::bigint""",
       ts=now, es=end_state, t80=m["t_80_min"], ttg=m["t_target_min"], ov=m["overshoot_c"], tl=m["tail_slope"],
       sd=m["settle_sd"], pk=m["peak_c"], v="true" if valid else "false", ir="; ".join(reasons), r=run["id"])
    log(f"huum: run {run['id']} ended: t80 {m['t_80_min']} min, target {m['t_target_min']} min, "
        f"overshoot {m['overshoot_c']}, tail {m['tail_slope']}, settle sd {m['settle_sd']}, "
        f"{'valid' if valid else 'INVALID: ' + '; '.join(reasons)}")


# --- HUUM -------------------------------------------------------------------------------
def poll_huum(env, state):
    user, pw = env.get("HUUM_USERNAME"), env.get("HUUM_PASSWORD")
    if not (user and pw):
        log("huum: no credentials in .env, skipping")
        return
    now = int(time.time())
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    try:
        s = get_json(HUUM_STATUS, {"Authorization": f"Basic {auth}", "Accept": "application/json"})
    except (urllib.error.URLError, ValueError, OSError) as exc:
        log(f"huum: poll failed: {exc}")
        write_prom("iot-huum", [f"huum_poll_success 0", f"huum_last_poll_timestamp {now}"])
        return

    code = int(s.get("statusCode") or 0)
    temp = float(s.get("temperature") or 0)
    target = float(s.get("targetTemperature") or 0)
    door_closed = 1 if s.get("door") else 0          # the API's `door` is true when closed
    end_ts = int(s.get("endDate") or 0)
    start_ts = int(s.get("startDate") or 0)
    heating = 1 if code == 231 else 0
    # The controller reports 230 ("offline") for long stretches while its readings keep
    # arriving -- the whole of the 2026-10-16 morning session, which the poller therefore
    # never saw as a session or a run. A session the API still describes (a target, an end in
    # the future) counts as heating for the session, run and armed tracking below; the strict
    # gauge keeps the controller's own word, and huum_session_live shows the difference.
    # A start in the future is a session armed on the panel, not one heating: without the
    # start check, run 3 on 2026-10-16 opened at arming time and read 23 minutes slow.
    session_live = 1 if (target > 0 and end_ts > now and 0 < start_ts <= now) else 0
    heating_like = 1 if (heating or (code == 230 and session_live)) else 0
    lines = [
        f"huum_poll_success 1",
        f"huum_last_poll_timestamp {now}",
        f"huum_status_code {code}",
        f'huum_status{{state="{HUUM_CODES.get(code, "unknown")}"}} 1',
        f"huum_heating {heating}",
        f"huum_session_live {session_live}",
        f"huum_temperature_celsius {temp}",
        f"huum_target_celsius {target}",
        f"huum_door_closed {door_closed}",
        f"huum_session_end_timestamp {end_ts}",
        f"huum_steamer_error {1 if s.get('steamerError') else 0}",
        f"huum_armed {1 if (target > 0 and heating == 0 and code != 230) else 0}",
    ]
    write_prom("iot-huum", lines)

    # Session tracking: a session starts when the heater reports heating and ends when it
    # stops. Peak temperature is tracked across polls; the session row is written once.
    h = state.setdefault("huum", {})
    active = h.get("active")
    if heating_like:
        if not active:
            active = {"started_ts": start_ts or now, "target": target, "peak": temp}
            h["active"] = active
            log(f"huum: session started, target {target:g}°C")
        active["peak"] = max(active.get("peak", 0), temp)
        active["target"] = target or active.get("target")
        active["last_temp"] = temp
    elif active:
        started = active["started_ts"]
        minutes = max(1, round((now - started) / 60))
        detail = {"minutes": minutes, "peak_c": active.get("peak"), "target_c": active.get("target"),
                  "end_state": HUUM_CODES.get(code, "unknown")}
        record_event("sauna", "session", "ok" if code != 400 else "error", started=iso(started), detail=detail)
        log(f"huum: session ended after {minutes} min, peak {active.get('peak')}°C")
        h["active"] = None
    if code == 400:
        log("huum: EMERGENCY STOP reported")

    # The heat-up experiment's run and its one-minute samples (this runs every minute from
    # cron as --poll-huum). A cloud blip (230) ends the run like it ends the session; the
    # session that follows is a warm start and is marked invalid by itself.
    run = h.get("run")
    if heating_like:
        if not run:
            run_start(h, s, now, temp, target)
            run = h["run"]
        run_sample(run, now, temp, target, door_closed, code)
    elif run:
        run_finish(run, now, HUUM_CODES.get(code, "unknown"))
        h["run"] = None

    # Scheduled-start tracking. A target temperature with no heating means a session is
    # armed (a delayed start set on the controller or in the app). If that target goes back
    # to zero before any heating happened, the schedule was lost -- which is what the
    # controller did on 2026-10-06, four minutes after reconnecting to its cloud -- or it was
    # cancelled by hand; the event says how long it had been armed so the two can be told
    # apart. While the controller is offline (230) nothing is concluded: its target reads 0
    # only because nobody can read it.
    armed = h.get("armed")
    if heating_like:
        if armed:
            h["armed"] = None            # started as planned
    elif code == 230:
        pass
    elif target > 0:
        if not armed:
            h["armed"] = {"since_ts": now, "target": target}
            log(f"huum: session armed, target {target:g}°C")
        else:
            armed["target"] = target
    elif armed:
        minutes = max(1, round((now - armed["since_ts"]) / 60))
        detail = {"target_c": armed.get("target"), "armed_minutes": minutes,
                  "state": HUUM_CODES.get(code, "unknown")}
        record_event("sauna", "schedule-lost", "error", started=iso(armed["since_ts"]), detail=detail)
        log(f"huum: ARMED SESSION VANISHED without heating after {minutes} min (target {armed.get('target')}°C)")
        h["armed"] = None


# --- Rachio -----------------------------------------------------------------------------
def poll_rachio(env, state):
    key = env.get("RACHIO_API_KEY")
    if not key:
        log("rachio: no API key in .env, skipping")
        return
    hdr = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    now = int(time.time())
    r = state.setdefault("rachio", {})
    try:
        if not r.get("person_id"):
            r["person_id"] = get_json(f"{RACHIO}/person/info", hdr)["id"]
        person = get_json(f"{RACHIO}/person/{r['person_id']}", hdr)
        devices = person.get("devices") or []
        if not devices:
            log("rachio: account has no devices")
            return
        dev = devices[0]
        r["device_id"] = dev["id"]
        current = get_json(f"{RACHIO}/device/{dev['id']}/current_schedule", hdr)
        since_ms = int((r.get("events_cursor_ms") or (now - 86400) * 1000))
        events = get_json(f"{RACHIO}/device/{dev['id']}/event?startTime={since_ms}&endTime={now * 1000}", hdr)
    except (urllib.error.URLError, ValueError, OSError, KeyError) as exc:
        log(f"rachio: poll failed: {exc}")
        write_prom("iot-rachio", ["rachio_poll_success 0", f"rachio_last_poll_timestamp {now}"])
        return

    zones = [z for z in dev.get("zones", []) if z.get("enabled")]
    rain_delay_ms = int(dev.get("rainDelayExpirationDate") or 0)
    running = 1 if current and current.get("status") in ("PROCESSING", "RUNNING") or (current and current.get("zoneId")) else 0
    lines = [
        "rachio_poll_success 1",
        f"rachio_last_poll_timestamp {now}",
        f"rachio_online {1 if dev.get('status') == 'ONLINE' else 0}",
        f"rachio_zones_enabled {len(zones)}",
        f"rachio_rain_delay_expiry_timestamp {rain_delay_ms // 1000}",
        f"rachio_rain_delay_active {1 if rain_delay_ms // 1000 > now else 0}",
        f"rachio_watering {running}",
        f'rachio_device_info{{name="{dev.get("name", "")}",model="{dev.get("model", "")}"}} 1',
    ]
    for z in zones:
        lines.append(f'rachio_zone_last_watered_timestamp{{zone="{z.get("zoneNumber")}",name="{z.get("name", "")}"}} {int(z.get("lastWateredDate") or 0) // 1000}')
    write_prom("iot-rachio", lines)

    newest = since_ms
    for ev in sorted(events, key=lambda e: e.get("eventDate", 0)):
        sub = ev.get("subType") or ev.get("type")
        newest = max(newest, int(ev.get("eventDate") or 0))
        if sub not in RACHIO_EVENTS:
            continue
        record_event("sprinkler", sub.lower(), RACHIO_EVENTS[sub],
                     started=iso(int(ev["eventDate"]) // 1000),
                     detail={"summary": ev.get("summary"), "category": ev.get("category"), "type": ev.get("type")},
                     source_id=f"rachio:{ev.get('id')}")
    # Re-read the last hour on every poll so a late-arriving event is not missed; source_id
    # keeps the re-read idempotent.
    r["events_cursor_ms"] = max(since_ms, newest - 3600 * 1000)


def main():
    modes = {"--poll": ("huum", "rachio"), "--poll-huum": ("huum",), "--poll-rachio": ("rachio",)}
    chosen = [m for m in sys.argv[1:] if m in modes]
    if not chosen and not DRY:
        print(__doc__)
        return 2
    which = modes[chosen[0]] if chosen else modes["--poll"]
    env = load_env()
    state = load_state()
    if "huum" in which:
        poll_huum(env, state)
    if "rachio" in which:
        poll_rachio(env, state)
    save_state(state, which)
    return 0


if __name__ == "__main__":
    sys.exit(main())
