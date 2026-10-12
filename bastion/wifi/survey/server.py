#!/usr/bin/env python3
"""wifi-survey — a phone-sized page for walking the house and recording WiFi coverage.

    https://wifi.robbywhite.net   (LAN only, through Caddy; see docs/plan-wifi.md, "Coverage survey")

iOS lets neither a web page nor an ordinary app read the phone's own WiFi signal, so this
shows the *controller's* view instead: Omada knows which AP the phone is on, on which band,
and how loudly the AP hears it -- the same numbers roaming and the wifi-weak-signal rule run
on. The page identifies the device by the address Caddy forwards (X-Forwarded-For), looks it
up in Omada's client list, and shows AP / band / RSSI live. "Mark" runs a download test
against this server and writes one row to wifi_survey with the spot's name; the Results tab
and the Grafana "WiFi survey" dashboard read those rows back. The Clients tab is the whole
wireless client list, worst signal first, for the same reason: live on a phone beats Grafana.

Reads Omada with the Viewer app (OMADA_CLIENT_ID/SECRET -- never the Operator's) and, while the
AC Lite is still in service, UniFi with a View Only local admin (UNIFI_USER/PASSWORD; both lists
are merged, each client tagged with its controller). Writes only
the wifi_survey table (EVENTS_DB_PASSWORD), and runs as nobody in its own container on the
observability and vault networks (events-db by name; Caddy by name). Nothing here needs
authentication beyond "on the LAN": the worst a LAN user can do is add or delete survey rows.

    GET  /                 the page
    GET  /api/me           this device's current reading (by forwarded IP)
    GET  /api/clients      every wireless client with name, AP, band, RSSI
    GET  /api/survey       per-spot summary and the latest rows
    POST /api/mark         {spot, note, download_mbps} -> one row
    DELETE /api/survey?id= remove a mistaken mark
    GET  /blob?mb=N        N MB of random bytes for the download test (max 100)
"""
import json
import os
import random
import ssl
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pg8000.native

OMADA = os.environ.get("OMADA_URL", "https://192.168.0.10:8043")
CLIENT_ID, CLIENT_SECRET = os.environ["OMADA_CLIENT_ID"], os.environ["OMADA_CLIENT_SECRET"]
DB = dict(host=os.environ.get("DB_HOST", "events-db"), user="homelab",
          password=os.environ["EVENTS_DB_PASSWORD"], database="homelab", timeout=10)
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PORT = int(os.environ.get("PORT", "8080"))

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE            # the controller's own self-signed certificate
OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
BAND = {0: "2.4", 1: "5", 2: "5", 3: "6"}   # Omada radioId; 1 and 2 are both 5 GHz on tri-band
TOKEN_ERRORS = {-44112, -44113, -44114, -44116}   # invalid / expired access token
BLOB = random.randbytes(1 << 20)           # 1 MB of noise, repeated: incompressible on purpose


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%S%z") + " [wifi-survey] " + msg, flush=True)


# --- Omada --------------------------------------------------------------------------------
class Omada:
    def __init__(self):
        self.lock = threading.Lock()
        self.token, self.token_exp = None, 0
        self.omadac, self.site = None, None
        self.cache = (0.0, [])

    def _request(self, url, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            hdrs["Authorization"] = f"AccessToken={token}"
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
        with OPENER.open(req, timeout=10) as resp:
            return json.load(resp)

    def _token(self, fresh=False):
        with self.lock:
            if self.token and not fresh and time.time() < self.token_exp:
                return self.token
            if not self.omadac:
                self.omadac = self._request(f"{OMADA}/api/info")["result"]["omadacId"]
            tok = self._request(f"{OMADA}/openapi/authorize/token?grant_type=client_credentials",
                                {"omadacId": self.omadac, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
            if tok.get("errorCode") != 0:
                raise RuntimeError(f"omada token: {tok.get('msg')}")
            self.token = tok["result"]["accessToken"]
            self.token_exp = time.time() + int(tok["result"].get("expiresIn", 7200)) - 60
            return self.token

    def get(self, path):
        """GET an OpenAPI path under /openapi/v1/{omadacId}, refreshing the token once if rejected."""
        for attempt in (1, 2):
            r = self._request(f"{OMADA}/openapi/v1/{self.omadac}{path}", token=self._token(fresh=attempt == 2))
            if r.get("errorCode") in TOKEN_ERRORS and attempt == 1:
                continue
            if r.get("errorCode") != 0:
                raise RuntimeError(f"omada {path}: {r.get('msg')}")
            return r.get("result") or {}
        return {}

    def clients(self):
        """Wireless clients as the controller sees them now; cached for three seconds."""
        ts, cached = self.cache
        if time.time() - ts < 3:
            return cached
        self._token()
        if not self.site:
            self.site = self.get("/sites?page=1&pageSize=10")["data"][0]["siteId"]
        out, page = [], 1
        while True:
            res = self.get(f"/sites/{self.site}/clients?page={page}&pageSize=200")
            data = res.get("data") or []
            for c in data:
                if not c.get("wireless"):
                    continue
                out.append({"mac": (c.get("mac") or "").replace("-", ":").lower(), "ip": c.get("ip"),
                            "omada_name": c.get("name") or c.get("hostName"),
                            "ap": c.get("apName") or (c.get("apMac") or "").replace("-", ":").lower(),
                            "ssid": c.get("ssid"), "band": BAND.get(c.get("radioId"), "?"),
                            "rssi": c.get("rssi"), "uptime": c.get("uptime")})
            if len(data) < 200:
                break
            page += 1
        self.cache = (time.time(), out)
        return out


OMADA_API = Omada()


# --- UniFi (the AC Lite, while it lasts) -----------------------------------------------------
UNIFI = os.environ.get("UNIFI_URL", "https://192.168.0.10:8443")
UNIFI_USER, UNIFI_PASSWORD = os.environ.get("UNIFI_USER"), os.environ.get("UNIFI_PASSWORD")
UNIFI_BAND = {"ng": "2.4", "na": "5", "6e": "6"}
_unifi_cache = (0.0, [])


def unifi_clients():
    """Wireless clients on the UniFi side, same shape as Omada's; empty when no credential is set.

    The legacy local API with a View Only admin (log in, read, log out), as wifi-clients.py
    does. Cached for three seconds like the Omada list; a failure returns the stale list and
    logs once, so the page keeps working on Omada alone.
    """
    global _unifi_cache
    if not (UNIFI_USER and UNIFI_PASSWORD):
        return []
    ts, cached = _unifi_cache
    if time.time() - ts < 3:
        return cached
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX),
                                         urllib.request.HTTPCookieProcessor(jar))

    def req(path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        hdrs.update(headers or {})
        r = urllib.request.Request(f"{UNIFI}{path}", data=data, headers=hdrs, method="POST" if data is not None else "GET")
        with opener.open(r, timeout=10) as resp:
            return json.load(resp)

    out = []
    try:
        if (req("/api/login", {"username": UNIFI_USER, "password": UNIFI_PASSWORD, "remember": False}).get("meta") or {}).get("rc") != "ok":
            raise RuntimeError("unifi login refused")
        try:
            for site in req("/api/self/sites").get("data") or []:
                aps = {d.get("mac", "").lower(): d.get("name") or d.get("model") or d.get("mac")
                       for d in req(f"/api/s/{site['name']}/stat/device").get("data") or []}
                for c in req(f"/api/s/{site['name']}/stat/sta").get("data") or []:
                    if c.get("is_wired"):
                        continue
                    out.append({"mac": (c.get("mac") or "").lower(), "ip": c.get("ip"),
                                "omada_name": c.get("hostname") or c.get("name"),
                                "ap": aps.get((c.get("ap_mac") or "").lower(), c.get("ap_mac")),
                                "ssid": c.get("essid"), "band": UNIFI_BAND.get(c.get("radio"), c.get("radio") or "?"),
                                "rssi": c.get("signal"), "uptime": c.get("uptime"), "controller": "unifi"})
        finally:
            csrf = next((ck.value for ck in jar if ck.name == "csrf_token"), None)
            try:
                req("/api/logout", {}, {"X-Csrf-Token": csrf} if csrf else None)
            except (urllib.error.URLError, ValueError, OSError):
                pass
    except (urllib.error.URLError, RuntimeError, ValueError, OSError, KeyError) as exc:
        log(f"unifi: {exc}; using the last list")
        return cached
    _unifi_cache = (time.time(), out)
    return out


def all_clients():
    """Both controllers' wireless clients; Omada's carry controller=omada."""
    return [dict(c, controller="omada") for c in OMADA_API.clients()] + unifi_clients()


# --- the events database --------------------------------------------------------------------
def db():
    return pg8000.native.Connection(**DB)


_names = (0.0, {})


def names():
    """mac -> the name AdGuard/lan_clients knows the device by; cached for thirty seconds."""
    global _names
    if time.time() - _names[0] < 30:
        return _names[1]
    con = db()
    try:
        m = {mac: (name or host) for mac, name, host in con.run("select mac, name, hostname from lan_clients")}
    finally:
        con.close()
    _names = (time.time(), m)
    return m


def with_names(clients):
    nm = names()
    for c in clients:
        c["name"] = nm.get(c["mac"]) or c.get("omada_name") or c["mac"]
    return clients


def survey_summary():
    con = db()
    try:
        spots = con.run("""
            select spot, count(*)::int as marks, max(ts) as last_ts,
                   round(avg(rssi))::int as avg_rssi, min(rssi)::int as min_rssi,
                   round(avg(download_mbps))::int as avg_mbps,
                   (array_agg(ap order by ts desc))[1] as last_ap,
                   (array_agg(band order by ts desc))[1] as last_band,
                   (array_agg(rssi order by ts desc))[1] as last_rssi,
                   (array_agg(download_mbps order by ts desc))[1] as last_mbps
            from wifi_survey group by spot order by avg_rssi asc nulls last""")
        recent = con.run("""
            select id, ts, spot, note, device_name, ap, ssid, band, rssi, download_mbps
            from wifi_survey order by ts desc limit 200""")
    finally:
        con.close()
    cols_s = ["spot", "marks", "last_ts", "avg_rssi", "min_rssi", "avg_mbps", "last_ap", "last_band", "last_rssi", "last_mbps"]
    cols_r = ["id", "ts", "spot", "note", "device_name", "ap", "ssid", "band", "rssi", "download_mbps"]
    return {"spots": [dict(zip(cols_s, r)) for r in spots], "recent": [dict(zip(cols_r, r)) for r in recent]}


def insert_mark(spot, note, mbps, client, ip):
    con = db()
    try:
        row = con.run("""
            insert into wifi_survey (spot, note, device_mac, device_name, ip, ap, ssid, band, rssi, download_mbps, controller)
            values (:spot, :note, :mac, :name, :ip, :ap, :ssid, :band, :rssi, :mbps, :controller)
            returning id, ts""",
                      spot=spot, note=note or None, mac=client.get("mac") if client else None,
                      name=client.get("name") if client else None, ip=ip,
                      ap=client.get("ap") if client else None, ssid=client.get("ssid") if client else None,
                      band=client.get("band") if client else None, rssi=client.get("rssi") if client else None,
                      mbps=mbps, controller=client.get("controller") if client else None)
    finally:
        con.close()
    return {"id": row[0][0], "ts": row[0][1]}


def delete_mark(mark_id):
    con = db()
    try:
        con.run("delete from wifi_survey where id = :id", id=mark_id)
    finally:
        con.close()


# --- HTTP ------------------------------------------------------------------------------------
def jsonable(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "__float__") and not isinstance(o, (int, float)):
        return float(o)
    raise TypeError(str(type(o)))


class Handler(BaseHTTPRequestHandler):
    server_version = "wifi-survey/1"

    def log_message(self, fmt, *args):          # one line per request, no client-side noise
        if "/api/me" not in (args[0] if args else ""):
            log(f"{self.client_ip()} {fmt % args}")

    def client_ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() if fwd else None) or self.client_address[0]

    def send_json(self, obj, status=200):
        body = json.dumps(obj, default=jsonable).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, name, ctype):
        try:
            with open(os.path.join(STATIC, name), "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def me(self):
        ip = self.client_ip()
        client = next((c for c in with_names(all_clients()) if c.get("ip") == ip), None)
        return ip, client

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path in ("/", "/index.html"):
                return self.send_file("index.html", "text/html; charset=utf-8")
            if url.path == "/api/me":
                ip, client = self.me()
                return self.send_json({"ip": ip, "client": client, "ts": time.time()})
            if url.path == "/api/clients":
                cl = sorted(with_names(all_clients()),
                            key=lambda c: (c["rssi"] is None, c["rssi"] if c["rssi"] is not None else 0))
                return self.send_json({"clients": cl, "ts": time.time()})
            if url.path == "/api/survey":
                return self.send_json(survey_summary())
            if url.path == "/blob":
                mb = max(1, min(100, int((parse_qs(url.query).get("mb") or ["25"])[0])))
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(mb * len(BLOB)))
                self.end_headers()
                for _ in range(mb):
                    self.wfile.write(BLOB)
                return None
            self.send_error(404)
        except (urllib.error.URLError, RuntimeError, OSError, ValueError, KeyError, IndexError) as exc:
            log(f"ERROR {url.path}: {exc}")
            self.send_json({"error": str(exc)}, 500)
        return None

    def do_POST(self):
        url = urlparse(self.path)
        try:
            if url.path != "/api/mark":
                return self.send_error(404)
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(length, 10000)) or b"{}")
            spot = " ".join(str(body.get("spot") or "").split())[:60]
            if not spot:
                return self.send_json({"error": "spot is required"}, 400)
            note = " ".join(str(body.get("note") or "").split())[:200]
            mbps = body.get("download_mbps")
            mbps = round(float(mbps), 1) if mbps is not None else None
            ip, client = self.me()
            # The AP reports a client's RSSI every few seconds and the page polls it every three,
            # so one sample can be the reading from the previous room. The page sends the last
            # ~15 s of samples; the median of those is what gets recorded.
            samples = [int(s) for s in (body.get("rssi_samples") or [])[-10:] if s is not None]
            if client and len(samples) >= 3:
                client = dict(client, rssi=sorted(samples)[len(samples) // 2])
            row = insert_mark(spot, note, mbps, client, ip)
            log(f"mark {row['id']}: {spot!r} {client and client.get('name')} {client and client.get('ap')} "
                f"{client and client.get('band')} GHz {client and client.get('rssi')} dBm {mbps} Mbps")
            return self.send_json({"ok": True, "id": row["id"], "ts": row["ts"], "client": client, "download_mbps": mbps})
        except (urllib.error.URLError, RuntimeError, OSError, ValueError, KeyError) as exc:
            log(f"ERROR {url.path}: {exc}")
            return self.send_json({"error": str(exc)}, 500)

    def do_DELETE(self):
        url = urlparse(self.path)
        try:
            if url.path != "/api/survey":
                return self.send_error(404)
            mark_id = int((parse_qs(url.query).get("id") or ["0"])[0])
            delete_mark(mark_id)
            log(f"deleted mark {mark_id}")
            return self.send_json({"ok": True})
        except (RuntimeError, OSError, ValueError) as exc:
            return self.send_json({"error": str(exc)}, 500)


if __name__ == "__main__":
    log(f"listening on :{PORT}; omada {OMADA}; db {DB['host']}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
