#!/usr/bin/env python3
"""iot-poll.py — read the sauna and the sprinklers, publish gauges, record sessions and runs.

    iot-poll.py --poll       every 5 minutes from cron: gauges to the textfile collector,
                             finished sessions and runs to the events database
    iot-poll.py --dry-run    print what --poll would publish and record; write nothing

Both devices are read from their vendors' clouds (neither exposes a local API), with the
credentials in ../.env: HUUM_USERNAME/HUUM_PASSWORD (the app login, HTTP basic auth against
api.huum.eu) and RACHIO_API_KEY (from the app's Profile screen, bearer token).

What comes out:

  gauges  ~/observability/textfile/iot-huum.prom and iot-rachio.prom, read by node exporter
          -- the sauna's status code, temperature, target, door, session end; the sprinkler
          controller's online state, rain delay, whether a zone is running. These are the
          "right now" view that Grafana graphs.
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


def save_state(state):
    if DRY:
        return
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE + ".tmp", "w") as fh:
        json.dump(state, fh, indent=1)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)


def iso(ts_seconds):
    return datetime.fromtimestamp(int(ts_seconds), tz=timezone.utc).astimezone().isoformat()


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
    lines = [
        f"huum_poll_success 1",
        f"huum_last_poll_timestamp {now}",
        f"huum_status_code {code}",
        f'huum_status{{state="{HUUM_CODES.get(code, "unknown")}"}} 1',
        f"huum_heating {heating}",
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
    if heating:
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

    # Scheduled-start tracking. A target temperature with no heating means a session is
    # armed (a delayed start set on the controller or in the app). If that target goes back
    # to zero before any heating happened, the schedule was lost -- which is what the
    # controller did on 2026-10-06, four minutes after reconnecting to its cloud -- or it was
    # cancelled by hand; the event says how long it had been armed so the two can be told
    # apart. While the controller is offline (230) nothing is concluded: its target reads 0
    # only because nobody can read it.
    armed = h.get("armed")
    if heating:
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
    if "--poll" not in sys.argv and not DRY:
        print(__doc__)
        return 2
    env = load_env()
    state = load_state()
    poll_huum(env, state)
    poll_rachio(env, state)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
