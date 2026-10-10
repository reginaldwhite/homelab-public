#!/usr/bin/env python3
"""wifi-clients.py — the wireless half of the network map (docs/plan-wifi.md, phase 4).

    wifi-clients.py --poll       every 5 minutes: both controllers' client lists -> lan_clients
                                 (AP, SSID, band, signal, uptime) and gauges for Prometheus
    wifi-clients.py --dry-run    print what --poll would write instead of writing it
    wifi-clients.py --dump       print the raw client objects each controller returns, for
                                 checking the field mapping below after a controller upgrade

Two controllers, two APIs, both on bastion's host network with self-signed certificates, so
this talks to 127.0.0.1 only and skips certificate verification for exactly that reason.

  Omada   OpenAPI, client-credentials mode, an app with the Viewer role created in
          Omada -> Settings -> Platform Integration -> Open API.  OMADA_CLIENT_ID / _SECRET
  UniFi   the Network application's local API with a read-only local admin.
          UNIFI_USER / UNIFI_PASSWORD

A controller whose credential is missing from .env is skipped silently (the stack can be
deployed before the credentials exist); one whose poll fails publishes wifi_poll_ok 0, which
the wifi-poll-failed rule turns into a Slack message after 30 minutes.

Gauges, in ~/observability/textfile/wifi.prom:
  wifi_poll_ok{controller}                           1 when the last poll of that controller worked
  wifi_clients{controller}                           wireless clients it currently sees
  wifi_client_signal_dbm{controller,mac,name,ap,ssid,band}
  wifi_client_uptime_seconds{...same labels}
"""

import http.cookiejar
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
UNIFI = "https://127.0.0.1:8443"
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


def omada_clients(env):
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


# --- UniFi ----------------------------------------------------------------------------------
UNIFI_BAND = {"ng": "2.4", "na": "5", "6e": "6"}


def unifi_clients(env):
    user, pw = env.get("UNIFI_USER"), env.get("UNIFI_PASSWORD")
    if not (user and pw):
        return None
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX),
                                         urllib.request.HTTPCookieProcessor(jar))
    r = request(opener, f"{UNIFI}/api/login", {"username": user, "password": pw, "remember": False})
    if (r.get("meta") or {}).get("rc") != "ok":
        raise RuntimeError(f"unifi login: {r.get('meta')}")
    out = []
    try:
        for site in request(opener, f"{UNIFI}/api/self/sites").get("data") or []:
            name = site["name"]
            aps = {}
            for d in request(opener, f"{UNIFI}/api/s/{name}/stat/device").get("data") or []:
                aps[norm_mac(d.get("mac"))] = d.get("name") or d.get("model") or norm_mac(d.get("mac"))
            data = request(opener, f"{UNIFI}/api/s/{name}/stat/sta").get("data") or []
            if DUMP and data:
                print("--- unifi raw client:", json.dumps(data[0], indent=1)[:2500])
            for c in data:
                if c.get("is_wired"):
                    continue
                out.append({
                    "controller": "unifi", "mac": norm_mac(c.get("mac")), "ip": c.get("ip"),
                    "hostname": c.get("hostname") or c.get("name"),
                    "ap": aps.get(norm_mac(c.get("ap_mac")), norm_mac(c.get("ap_mac"))),
                    "ssid": c.get("essid"), "band": UNIFI_BAND.get(c.get("radio"), c.get("radio") or ""),
                    "signal": c.get("signal"), "uptime": c.get("uptime"),
                })
    finally:
        csrf = next((ck.value for ck in jar if ck.name == "csrf_token"), None)
        try:
            request(opener, f"{UNIFI}/api/logout", {}, {"X-Csrf-Token": csrf} if csrf else None)
        except (urllib.error.URLError, ValueError, OSError):
            pass
    return out


# --- outputs --------------------------------------------------------------------------------
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
    for name, fn in (("omada", omada_clients), ("unifi", unifi_clients)):
        try:
            got = fn(env)
        except (urllib.error.URLError, ValueError, OSError, KeyError, RuntimeError) as exc:
            log(f"{name}: poll failed: {exc}")
            lines.append(f'wifi_poll_ok{{controller="{name}"}} 0')
            continue
        if got is None:
            continue   # no credential yet
        rows.extend(got)
        lines.append(f'wifi_poll_ok{{controller="{name}"}} 1')
        lines.append(f'wifi_clients{{controller="{name}"}} {len(got)}')
    seen = set()
    for r in rows:
        if not r["mac"] or r["mac"] in seen:
            continue
        seen.add(r["mac"])
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
