#!/usr/bin/env python3
"""adguard-stats.py — AdGuard Home's numbers for Prometheus, and its client table for Postgres.

    adguard-stats.py --stats     every minute: totals, blocked, latency, top clients -> textfile
    adguard-stats.py --clients   every 5 minutes: DHCP leases and persistent clients -> lan_clients
    adguard-stats.py --dry-run   print instead of writing (combine with --stats / --clients)

Our own forty lines instead of a third-party exporter: the two well-known ones are archived,
and any of them would hold the AdGuard admin password. This reads ADGUARD_USER/ADGUARD_PASSWORD
from ../.env and talks to AdGuard on 127.0.0.1:8888 with HTTP basic auth.

AdGuard's /control/stats figures are totals over its statistics window (24 hours here), not
counters, so they are published as gauges and graphed as "queries in the last day".
"""

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(HERE), ".env")
TEXTFILE_DIR = "/home/robby/observability/textfile"
LOG = "/home/robby/logs/adguard-stats.log"
API = "http://127.0.0.1:8888/control"
DB_CONTAINER = "events-db"
DRY = "--dry-run" in sys.argv


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now().astimezone().isoformat()} [adguard-stats] {msg}\n")
    if DRY:
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


def api(path, auth):
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def label(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def write_prom(stem, lines):
    if DRY:
        print(f"--- {stem}.prom"); print("\n".join(lines)); return
    if not os.path.isdir(TEXTFILE_DIR):
        return
    path = os.path.join(TEXTFILE_DIR, f"{stem}.prom")
    with open(path + ".tmp", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)


def stats(auth):
    st = api("/stats", auth)
    status = api("/status", auth)
    dhcp = api("/dhcp/status", auth)
    names = {}
    for c in api("/clients", auth).get("clients") or []:
        for ident in c.get("ids", []):
            names[ident] = c.get("name")
    leases = (dhcp.get("leases") or []) + (dhcp.get("static_leases") or [])
    for l in leases:
        names.setdefault(l.get("ip"), l.get("hostname") or "")
        names.setdefault(l.get("mac"), l.get("hostname") or "")

    lines = [
        f'adguard_info{{version="{label(status.get("version"))}"}} 1',
        f"adguard_protection_enabled {1 if status.get('protection_enabled') else 0}",
        f"adguard_running {1 if status.get('running') else 0}",
        f"adguard_stats_queries {st.get('num_dns_queries', 0)}",
        f"adguard_stats_blocked {st.get('num_blocked_filtering', 0)}",
        f"adguard_stats_safebrowsing {st.get('num_replaced_safebrowsing', 0)}",
        f"adguard_stats_parental {st.get('num_replaced_parental', 0)}",
        f"adguard_stats_safesearch {st.get('num_replaced_safesearch', 0)}",
        f"adguard_avg_processing_seconds {float(st.get('avg_processing_time') or 0)}",
        f"adguard_stats_window_seconds {int(st.get('time_units') == 'hours' and 3600 * len(st.get('dns_queries') or []) or 86400)}",
        f"adguard_dhcp_enabled {1 if dhcp.get('enabled') else 0}",
        f"adguard_dhcp_leases {len(dhcp.get('leases') or [])}",
        f"adguard_dhcp_static_leases {len(dhcp.get('static_leases') or [])}",
    ]
    for entry in st.get("top_clients") or []:
        for ip, n in entry.items():
            lines.append(f'adguard_client_queries{{client="{label(ip)}",name="{label(names.get(ip, ""))}"}} {n}')
    for entry in (st.get("top_blocked_domains") or [])[:20]:
        for dom, n in entry.items():
            lines.append(f'adguard_blocked_domain_queries{{domain="{label(dom)}"}} {n}')
    write_prom("adguard", lines)


def sql_str(s):
    if s is None:
        return "null"
    s = "".join(ch for ch in str(s) if ch >= " " and ch != "\x7f")
    return "'" + s.replace("'", "''") + "'"


def clients(auth):
    dhcp = api("/dhcp/status", auth)
    persistent = api("/clients", auth).get("clients") or []
    rows = {}
    for l in dhcp.get("leases") or []:
        rows[l["mac"].lower()] = {"ip": l.get("ip"), "hostname": l.get("hostname"), "source": "dhcp"}
    for l in dhcp.get("static_leases") or []:
        rows[l["mac"].lower()] = {"ip": l.get("ip"), "hostname": l.get("hostname"), "source": "static"}
    for c in persistent:
        macs = [i.lower() for i in c.get("ids", []) if ":" in i and len(i) == 17]
        for m in macs:
            row = rows.setdefault(m, {"ip": None, "hostname": None, "source": "client"})
            row["name"] = c.get("name")
            row["tags"] = c.get("tags") or []
    if not rows:
        log("clients: nothing to write")
        return
    values = []
    for mac, r in rows.items():
        tags = "array[" + ",".join(sql_str(t) for t in r.get("tags") or []) + "]::text[]" if r.get("tags") else "null"
        values.append(f"({sql_str(mac)}, {sql_str(r.get('ip'))}, {sql_str(r.get('hostname'))}, {sql_str(r.get('name'))}, {tags}, {sql_str(r.get('source'))})")
    sql = ("insert into lan_clients (mac, ip, hostname, name, tags, source) values\n" + ",\n".join(values) +
           "\non conflict (mac) do update set ip = excluded.ip, hostname = coalesce(excluded.hostname, lan_clients.hostname), "
           "name = coalesce(excluded.name, lan_clients.name), tags = coalesce(excluded.tags, lan_clients.tags), "
           "source = excluded.source, last_seen = now();\n")
    if DRY:
        print(f"--- lan_clients upsert, {len(rows)} rows"); print(sql[:600] + ("…" if len(sql) > 600 else "")); return
    if subprocess.run(["docker", "inspect", DB_CONTAINER], capture_output=True).returncode != 0:
        return
    res = subprocess.run(["docker", "exec", "-i", DB_CONTAINER, "psql", "-q", "-U", "homelab", "-d", "homelab", "-v", "ON_ERROR_STOP=1"],
                         input=sql, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"clients: upsert failed: {res.stderr[:200]}")


def main():
    env = load_env()
    user, pw = env.get("ADGUARD_USER"), env.get("ADGUARD_PASSWORD")
    if not (user and pw):
        log("no ADGUARD_USER/ADGUARD_PASSWORD in .env")
        return 1
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    try:
        if "--stats" in sys.argv:
            stats(auth)
        if "--clients" in sys.argv:
            clients(auth)
        if not ({"--stats", "--clients"} & set(sys.argv)):
            print(__doc__); return 2
    except (urllib.error.URLError, ValueError, OSError, KeyError) as exc:
        log(f"failed: {exc}")
        write_prom("adguard", ["adguard_running 0"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
