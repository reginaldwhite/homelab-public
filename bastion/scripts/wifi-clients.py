#!/usr/bin/env python3
"""wifi-clients.py — the wireless half of the network map (docs/plan-wifi.md, phase 4).

    wifi-clients.py --poll       every 5 minutes: the controller's client list -> lan_clients
                                 (AP, SSID, band, signal, uptime) and gauges for Prometheus
    wifi-clients.py --dry-run    print what --poll would write instead of writing it
    wifi-clients.py --dump       print the raw client objects the controller returns, for
                                 checking the field mapping below after a controller upgrade

One controller on bastion's host network with a self-signed certificate, so this talks to
127.0.0.1 only and skips certificate verification for exactly that reason.

  Omada   OpenAPI, client-credentials mode, an app with the Viewer role created in
          Omada -> Settings -> Platform Integration -> Open API.  OMADA_CLIENT_ID / _SECRET

(The UniFi half, which read the AC Lite's controller, went with it on 2026-10-20.)

A missing credential in .env skips the poll silently (the stack can be deployed before the
credential exists); a poll that fails publishes wifi_poll_ok 0, which
the wifi-poll-failed rule turns into a Slack message after 30 minutes.

Gauges, in ~/observability/textfile/wifi.prom:
  wifi_poll_ok{controller}                           1 when the last poll of that controller worked
  wifi_clients{controller}                           wireless clients it currently sees
  wifi_client_signal_dbm{controller,mac,name,ap,ssid,band}
  wifi_client_uptime_seconds{...same labels}
  wifi_ap_uptime_seconds{controller,ap,mac}          Omada only: seconds since the AP last booted

The AP uptime exists because of 2026-10-11: the EAP772 rebooted ~30 times in a day on
firmware 1.3.2 and nothing said so. The client "uptime" Omada reports survives short drops,
so the client gauges never showed it; the AP's own uptime does, and the wifi-ap-rebooted
rule turns it into a timestamped Slack message.
"""
import re

import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(HERE), ".env")
TEXTFILE_DIR = "/home/robby/observability/textfile"
LOG = "/home/robby/logs/wifi-clients.log"
DB_CONTAINER = "events-db"
OMADA = "https://127.0.0.1:8043"
DRY = "--dry-run" in sys.argv
DUMP = "--dump" in sys.argv

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now().astimezone().isoformat()} [wifi-clients] {msg}\n")
    if DRY or DUMP:
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


def request(opener, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
    with opener.open(req, timeout=15) as resp:
        return json.load(resp)


def norm_mac(mac):
    return (mac or "").replace("-", ":").lower()


def label(s):
    return str(s if s is not None else "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


# --- Omada ----------------------------------------------------------------------------------
# radioId as Omada numbers it; 1 and 2 are both 5 GHz on tri-band units.
OMADA_BAND = {0: "2.4", 1: "5", 2: "5", 3: "6"}


def omada_uptime_seconds(text):
    """Omada's device uptime is a string like '1d 2h 3m 4s' or '29m 45s'; None if unparseable."""
    parts = re.findall(r"(\d+)\s*([dhms])", str(text or ""))
    if not parts:
        return None
    return sum(int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u] for n, u in parts)


def omada_clients(env, extra):
    """Omada's wireless clients as rows; appends the APs' own gauges to `extra`."""
    cid, secret = env.get("OMADA_CLIENT_ID"), env.get("OMADA_CLIENT_SECRET")
    if not (cid and secret):
        return None
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
    omadac = request(opener, f"{OMADA}/api/info")["result"]["omadacId"]
    tok = request(opener, f"{OMADA}/openapi/authorize/token?grant_type=client_credentials",
                  {"omadacId": omadac, "client_id": cid, "client_secret": secret})
    if tok.get("errorCode") != 0:
        raise RuntimeError(f"omada token: {tok.get('msg')}")
    auth = {"Authorization": f"AccessToken={tok['result']['accessToken']}"}
    sites = request(opener, f"{OMADA}/openapi/v1/{omadac}/sites?page=1&pageSize=100", headers=auth)
    out = []
    for site in (sites.get("result") or {}).get("data") or []:
        # The APs themselves: uptime is the only reboot signal the controller exposes to the
        # API (its event/alert endpoints returned nothing for a day of reboots).
        devs = request(opener, f"{OMADA}/openapi/v1/{omadac}/sites/{site['siteId']}/devices?page=1&pageSize=100", headers=auth)
        for d in (devs.get("result") or {}).get("data") or []:
            if DUMP:
                print("--- omada raw device:", json.dumps(d, indent=1)[:1500])
            up = omada_uptime_seconds(d.get("uptime"))
            if d.get("type") == "ap" and up is not None:
                extra.append(f'wifi_ap_uptime_seconds{{controller="omada",ap="{label(d.get("name") or norm_mac(d.get("mac")))}",'
                             f'mac="{label(norm_mac(d.get("mac")))}"}} {up}')
        page = 1
        while True:
            r = request(opener, f"{OMADA}/openapi/v1/{omadac}/sites/{site['siteId']}/clients?page={page}&pageSize=200", headers=auth)
            if r.get("errorCode") != 0:
                raise RuntimeError(f"omada clients: {r.get('msg')}")
            data = (r.get("result") or {}).get("data") or []
            if DUMP and data:
                print("--- omada raw client:", json.dumps(data[0], indent=1)[:2500])
            for c in data:
                if not c.get("wireless"):
                    continue
                rssi = c.get("rssi")
                if rssi is None and c.get("signalLevel") is not None:
                    # Omada's percentage, roughly linear between -90 and -30 dBm.
                    rssi = int(-90 + 0.6 * float(c["signalLevel"]))
                out.append({
                    "controller": "omada", "mac": norm_mac(c.get("mac")), "ip": c.get("ip"),
                    "hostname": c.get("hostName") or c.get("name"),
                    "ap": c.get("apName") or norm_mac(c.get("apMac")), "ssid": c.get("ssid"),
                    "band": OMADA_BAND.get(c.get("radioId"), str(c.get("radioId") or "")),
                    "signal": rssi, "uptime": c.get("uptime"),
                })
            if len(data) < 200:
                break
            page += 1
    return out


# --- outputs --------------------------------------------------------------------------------
def dedupe(rows):
    """One row per MAC, newest association first.

    Both APs broadcast every SSID, and for a moment after a roam the controller can list a
    device under both; while two controllers were polled it happened every afternoon. lan_clients is keyed by MAC, and a single INSERT
    carrying the same MAC twice fails outright -- "ON CONFLICT DO UPDATE command cannot affect
    row a second time" -- which loses every row of that poll, not just the duplicate (seen
    three times on 2026-10-12). The client's own uptime is seconds since it associated, so the
    shorter one is the live association and wins.
    """
    def newest_first(r):
        try:
            return (0, int(r.get("uptime")))
        except (TypeError, ValueError):
            return (1, 0)   # no uptime: only used if nothing better exists for that MAC

    best = {}
    for r in rows:
        if not r.get("mac"):
            continue
        if r["mac"] not in best or newest_first(r) < newest_first(best[r["mac"]]):
            best[r["mac"]] = r
    return list(best.values())


def sql_str(s):
    if s is None:
        return "null"
    s = "".join(ch for ch in str(s) if ch >= " " and ch != "\x7f")
    return "'" + s.replace("'", "''") + "'"


def sql_int(v):
    try:
        return str(int(v))
    except (TypeError, ValueError):
        return "null"


def upsert(rows):
    rows = [r for r in rows if r["mac"]]
    if not rows:
        return
    values = [f"({sql_str(r['mac'])}, {sql_str(r.get('ip'))}, {sql_str(r.get('hostname'))}, {sql_str(r['controller'])}, "
              f"{sql_str(r.get('ap'))}, {sql_str(r.get('ssid'))}, {sql_str(r.get('band'))}, {sql_int(r.get('signal'))}, {sql_int(r.get('uptime'))}, now())"
              for r in rows]
    # AdGuard owns hostname/name/last_seen; this only fills them for a device AdGuard has not
    # named, and never overwrites what it set.
    sql = ("insert into lan_clients (mac, ip, hostname, wifi_controller, ap, ssid, band, signal_dbm, wifi_uptime_s, wifi_seen) values\n"
           + ",\n".join(values) +
           "\non conflict (mac) do update set ip = coalesce(excluded.ip, lan_clients.ip), "
           "hostname = coalesce(lan_clients.hostname, excluded.hostname), wifi_controller = excluded.wifi_controller, "
           "ap = excluded.ap, ssid = excluded.ssid, band = excluded.band, signal_dbm = excluded.signal_dbm, "
           "wifi_uptime_s = excluded.wifi_uptime_s, wifi_seen = now();\n")
    if DRY:
        print(f"--- lan_clients upsert, {len(rows)} rows"); print(sql[:900] + ("…" if len(sql) > 900 else "")); return
    if subprocess.run(["docker", "inspect", DB_CONTAINER], capture_output=True).returncode != 0:
        return
    res = subprocess.run(["docker", "exec", "-i", DB_CONTAINER, "psql", "-q", "-U", "homelab", "-d", "homelab", "-v", "ON_ERROR_STOP=1"],
                         input=sql, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"upsert failed: {res.stderr[:300]}")


def write_prom(lines):
    if DRY:
        print("--- wifi.prom"); print("\n".join(lines)); return
    if not os.path.isdir(TEXTFILE_DIR):
        return
    path = os.path.join(TEXTFILE_DIR, "wifi.prom")
    with open(path + ".tmp", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)


def main():
    if not ({"--poll", "--dry-run", "--dump"} & set(sys.argv)):
        print(__doc__); return 2
    env = load_env()
    rows, lines = [], []
    for name, fn in (("omada", omada_clients),):
        extra = []
        try:
            got = fn(env, extra)
        except (urllib.error.URLError, ValueError, OSError, KeyError, RuntimeError) as exc:
            log(f"{name}: poll failed: {exc}")
            lines.append(f'wifi_poll_ok{{controller="{name}"}} 0')
            continue
        if got is None:
            continue   # no credential yet
        rows.extend(got)
        lines.append(f'wifi_poll_ok{{controller="{name}"}} 1')
        lines.append(f'wifi_clients{{controller="{name}"}} {len(got)}')
        lines.extend(extra)
    rows = dedupe(rows)
    for r in rows:
        labels = (f'controller="{r["controller"]}",mac="{label(r["mac"])}",name="{label(r.get("hostname") or r["mac"])}",'
                  f'ap="{label(r.get("ap"))}",ssid="{label(r.get("ssid"))}",band="{label(r.get("band"))}"')
        if r.get("signal") is not None:
            lines.append(f"wifi_client_signal_dbm{{{labels}}} {int(r['signal'])}")
        if r.get("uptime") is not None:
            lines.append(f"wifi_client_uptime_seconds{{{labels}}} {int(r['uptime'])}")
    if DUMP:
        return 0
    write_prom(lines)
    upsert(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
