#!/usr/bin/env python3
"""omada-ssid.py — read and maintain the Omada site's SSIDs, schedules and MAC groups.

Reads use the Viewer credential wifi-clients.py already has (OMADA_CLIENT_ID/_SECRET); writes
need OMADA_ADMIN_CLIENT_ID/_SECRET, an Open API app with the Operator role. Without the admin
pair every writing command stops with a clear message. Every write takes --dry-run.

    omada-ssid.py list                         every SSID: bands, security, guest, schedule, MAC filter
    omada-ssid.py show <ssid>                  the controller's full record (password never printed)
    omada-ssid.py profiles                     time-range profiles and MAC groups, with ids

    omada-ssid.py set <ssid> [--bands 2.4,5,6] [--guest on|off] [--broadcast on|off]
                                               change bands / guest isolation / broadcast; everything
                                               else is re-sent exactly as the controller has it
    omada-ssid.py schedule <ssid> <time-range>|off [--mode on|off]
                                               radio ON during the time range's periods (default), or
                                               OFF during them (--mode off)
    omada-ssid.py mac-filter <ssid> deny|allow <mac-group>
    omada-ssid.py mac-filter <ssid> off
    omada-ssid.py time-range create <name> --span "mon-thu 05:30-22:00" [--span "fri-sat 05:30-23:00"]
                                               the periods; a span over midnight is split into two
                                               entries; minutes must be :00 :15 :30 :45. Omada caps
                                               the entries per profile, so prefer describing the ON
                                               hours (one entry per day) over the OFF hours; a span
                                               given as "all HH:MM-HH:MM" uses the every-day mode and
                                               costs one entry, not seven
    omada-ssid.py time-range delete <name>
    omada-ssid.py mac-group create|set <name> --mac label=aa:bb:cc:f3:32:a3 [--mac …]
    omada-ssid.py mac-group delete <name>
    omada-ssid.py clone <ssid> <new-name> [--groups Default,Office]
                                               a new SSID with the source's bands, security, guest
                                               isolation and PSK settings, bound to the named AP
                                               groups (default: the source's). The controller never
                                               returns a password, so the new one is taken from
                                               OMADA_SSID_PASSWORD in the environment
    omada-ssid.py ap-groups                    every AP group: which APs are in it, which SSIDs it carries
    omada-ssid.py ap-group create <group>      a new, empty AP group
    omada-ssid.py ap-group move <group> --ap <mac>
                                               put an AP in the group (an AP is in exactly one group, so
                                               this also takes it out of the one it was in)
    omada-ssid.py ap-group bind <ssid> --groups Default,Closet
                                               the groups whose APs broadcast the SSID
    An AP only broadcasts the SSIDs bound to ITS group, so the safe order to pin an SSID to one
    AP is: create the group empty, bind every other SSID to both groups, bind the one SSID to
    the new group alone, and only then move the AP in. Moving first strips the AP of every SSID
    not yet bound to the new group (2026-10-22).

The SSID design these commands implement is the table at the top of docs/lan-clients.md.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(HERE), ".env")
LOG = "/home/robby/logs/omada-ssid.log"
OMADA = "https://127.0.0.1:8043"
BAND_BITS = {"2.4": 1, "5": 2, "6": 4}
SECURITY = {0: "open", 2: "WPA-Enterprise", 3: "WPA-Personal", 4: "PPSK", 5: "PPSK+RADIUS"}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]   # Omada dayType 1..7

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now().astimezone().isoformat()} [omada-ssid] {msg}\n")


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


class Omada:
    def __init__(self, env, write):
        cid = env.get("OMADA_ADMIN_CLIENT_ID") if write else None
        sec = env.get("OMADA_ADMIN_CLIENT_SECRET") if write else None
        if write and not (cid and sec):
            sys.exit("this command writes to the controller and needs OMADA_ADMIN_CLIENT_ID / "
                     "OMADA_ADMIN_CLIENT_SECRET in bastion's .env (an Open API app with the Operator role)")
        cid = cid or env.get("OMADA_CLIENT_ID")
        sec = sec or env.get("OMADA_CLIENT_SECRET")
        if not (cid and sec):
            sys.exit("no OMADA_CLIENT_ID / OMADA_CLIENT_SECRET in bastion's .env")
        self.op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
        self.omadac = self.req("GET", "/api/info")["result"]["omadacId"]
        tok = self.req("POST", "/openapi/authorize/token?grant_type=client_credentials",
                       {"omadacId": self.omadac, "client_id": cid, "client_secret": sec})
        if tok.get("errorCode") != 0:
            sys.exit(f"omada token: {tok.get('msg')}")
        self.auth = {"Authorization": f"AccessToken={tok['result']['accessToken']}"}
        sites = self.api("GET", "/sites?page=1&pageSize=100", site=False)["result"]["data"]
        self.site = sites[0]["siteId"]
        self.site_name = sites[0]["name"]

    def req(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        hdrs.update(headers or {})
        r = urllib.request.Request(OMADA + path, data=data, headers=hdrs, method=method)
        try:
            with self.op.open(r, timeout=20) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            sys.exit(f"{method} {path}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}")

    def api(self, method, path, body=None, site=True):
        base = f"/openapi/v1/{self.omadac}" + (f"/sites/{self.site}" if site else "")
        r = self.req(method, base + path, body, self.auth)
        if r.get("errorCode") != 0:
            sys.exit(f"{method} {path}: {r.get('errorCode')} {r.get('msg')}")
        return r

    # --- lookups ---------------------------------------------------------------------------
    def wlan_id(self):
        groups = self.api("GET", "/wireless-network/wlans")["result"]
        return next(g["wlanId"] for g in groups if g.get("primary")) if groups else sys.exit("no WLAN group")

    def ssids(self):
        wid = self.wlan_id()
        out = []
        for s in self.api("GET", f"/wireless-network/wlans/{wid}/ssids?page=1&pageSize=50")["result"]["data"]:
            s["wlanId"] = wid
            out.append(s)
        return out

    def ssid(self, name):
        for s in self.ssids():
            if s["name"] == name:
                d = self.api("GET", f"/wireless-network/wlans/{s['wlanId']}/ssids/{s['ssidId']}")["result"]
                d["wlanId"] = s["wlanId"]
                d["ssidId"] = s["ssidId"]
                return d
        sys.exit(f"no SSID named {name!r} (have: {', '.join(x['name'] for x in self.ssids())})")

    def ap_groups(self):
        return self.api("GET", "/ap-groups?page=1&pageSize=50")["result"]["data"]

    def ap_group(self, name):
        for g in self.ap_groups():
            if g.get("name") == name:
                return g
        sys.exit(f"no AP group named {name!r} (have: {', '.join(g['name'] for g in self.ap_groups())})")

    def aps(self):
        return self.api("GET", "/ap-groups/aps?page=1&pageSize=50")["result"]["data"]

    def time_ranges(self):
        r = self.api("GET", "/time-range-profiles")["result"]
        return r if isinstance(r, list) else r.get("data", [])

    def mac_groups(self):
        r = self.api("GET", "/profiles/groups/2")["result"]
        return r if isinstance(r, list) else r.get("data", [])

    def time_range_id(self, name):
        for t in self.time_ranges():
            if t.get("name") == name:
                return t.get("profileId") or t.get("id")
        sys.exit(f"no time-range profile named {name!r}")

    def mac_group(self, name):
        for g in self.mac_groups():
            if g.get("name") == name:
                return g
        return None


def bands_str(bits):
    return "+".join(b for b, bit in BAND_BITS.items() if bits & bit) or "none"


def bands_bits(text):
    bits = 0
    for part in text.split(","):
        part = part.strip()
        if part not in BAND_BITS:
            sys.exit(f"band must be from 2.4,5,6 (got {part!r})")
        bits |= BAND_BITS[part]
    return bits


def scrub(d):
    """The controller's record without the password, for printing."""
    d = json.loads(json.dumps(d))
    if isinstance(d.get("pskSetting"), dict):
        d["pskSetting"].pop("securityKey", None)
    return d


# --- commands ------------------------------------------------------------------------------
def cmd_list(o, a):
    trs = {t.get("profileId") or t.get("id"): t.get("name") for t in o.time_ranges()}
    mgs = {g.get("groupId") or g.get("id"): g.get("name") for g in o.mac_groups()}
    print(f"site {o.site_name}")
    print(f"{'SSID':22} {'bands':10} {'security':14} {'guest':6} {'schedule':28} mac filter")
    for s in o.ssids():
        d = o.api("GET", f"/wireless-network/wlans/{s['wlanId']}/ssids/{s['ssidId']}")["result"]
        ws = d.get("wlanSchedule") or {}
        sched = "-"
        if ws.get("wlanScheduleEnable"):
            sched = f"{'off' if ws.get('action') == 0 else 'on'} during {trs.get(ws.get('scheduleId'), ws.get('scheduleId'))}"
        mf = d.get("macFilter") or {}
        macf = "-"
        if mf.get("macFilterEnable"):
            macf = f"{'deny' if mf.get('policy') == 0 else 'allow'} {mgs.get(mf.get('macFilterId'), mf.get('macFilterId'))}"
        print(f"{d['name']:22} {bands_str(d['band']):10} {SECURITY.get(d['security'], d['security']):14} "
              f"{'on' if d.get('guestNetEnable') else 'off':6} {sched:28} {macf}")


def cmd_show(o, a):
    print(json.dumps(scrub(o.ssid(a.ssid)), indent=1))


def cmd_profiles(o, a):
    print("time-range profiles:")
    for t in o.time_ranges():
        print(f"  {t.get('name')}  id={t.get('profileId') or t.get('id')}")
        for seg in t.get("timeList", []):
            print(f"     {DAYS[seg['dayType'] - 1] if seg.get('dayType') else 'all':3} {seg['startTimeH']:02d}:{seg['startTimeM']:02d}-{seg['endTimeH']:02d}:{seg['endTimeM']:02d}")
    print("MAC groups:")
    for g in o.mac_groups():
        print(f"  {g.get('name')}  id={g.get('groupId') or g.get('id')}")
        for m in g.get("macAddressList", []):
            print(f"     {m.get('macAddress')}  {m.get('name')}")


def write(o, a, method, path, body, what):
    if a.dry_run:
        print(f"DRY RUN {method} {path}\n{json.dumps(scrub(body), indent=1)}")
        return
    o.api(method, path, body)
    log(what)
    print(what)


def cmd_set(o, a):
    d = o.ssid(a.ssid)
    body = {k: d[k] for k in ("name", "band", "broadcast", "guestNetEnable", "security", "vlanEnable",
                              "mloEnable", "pmfMode", "enable11r") if k in d}
    for k in ("pskSetting", "entSetting", "ppskSetting", "hidePwd", "oweEnable", "autoWanAccess",
              "prohibitWifiShare", "greEnable"):
        if d.get(k) is not None:
            body[k] = d[k]
    # The controller refuses vlanId/vlanSetting when VLAN is off ("Cannot set vlanId and
    # customConfig when the mode of vlanSetting is 0"), so they go only with vlanEnable.
    if d.get("vlanEnable"):
        for k in ("vlanId", "vlanSetting"):
            if d.get(k) is not None:
                body[k] = d[k]
    changes = []
    if a.bands:
        body["band"] = bands_bits(a.bands); changes.append(f"bands {bands_str(d['band'])} -> {bands_str(body['band'])}")
    if a.guest:
        body["guestNetEnable"] = a.guest == "on"; changes.append(f"guest {d.get('guestNetEnable')} -> {body['guestNetEnable']}")
    if a.broadcast:
        body["broadcast"] = a.broadcast == "on"; changes.append(f"broadcast {d.get('broadcast')} -> {body['broadcast']}")
    if not changes:
        sys.exit("nothing to change (use --bands / --guest / --broadcast)")
    if body.get("security") == 3 and not (body.get("pskSetting") or {}).get("securityKey"):
        # The controller may withhold the key in its own record; it can be supplied from an env var.
        key = os.environ.get("OMADA_SSID_PASSWORD")
        if not key:
            sys.exit("the controller did not return this SSID's password and the update must carry it: "
                     "re-run with OMADA_SSID_PASSWORD=<password> in the environment")
        body.setdefault("pskSetting", {})["securityKey"] = key
    write(o, a, "PATCH", f"/wireless-network/wlans/{d['wlanId']}/ssids/{d['ssidId']}/update-basic-config", body,
          f"{a.ssid}: " + "; ".join(changes))


def cmd_schedule(o, a):
    d = o.ssid(a.ssid)
    path = f"/wireless-network/wlans/{d['wlanId']}/ssids/{d['ssidId']}/update-wlan-schedule"
    if a.time_range == "off":
        write(o, a, "PATCH", path, {"wlanScheduleEnable": False}, f"{a.ssid}: schedule off")
    else:
        tid = o.time_range_id(a.time_range)
        action = 1 if a.mode == "on" else 0
        write(o, a, "PATCH", path, {"wlanScheduleEnable": True, "action": action, "scheduleId": tid},
              f"{a.ssid}: radio {'on' if action else 'off'} during time range {a.time_range}")


def cmd_mac_filter(o, a):
    d = o.ssid(a.ssid)
    path = f"/wireless-network/wlans/{d['wlanId']}/ssids/{d['ssidId']}/update-mac-filter"
    if a.policy == "off":
        write(o, a, "PATCH", path, {"macFilterEnable": False}, f"{a.ssid}: MAC filter off")
        return
    g = o.mac_group(a.group) or sys.exit(f"no MAC group named {a.group!r}")
    write(o, a, "PATCH", path, {"macFilterEnable": True, "policy": 0 if a.policy == "deny" else 1,
                                "macFilterId": g.get("groupId") or g.get("id")},
          f"{a.ssid}: MAC filter {a.policy} group {a.group}")


def cmd_clone(o, a):
    d = o.ssid(a.ssid)
    body = {k: d[k] for k in ("band", "broadcast", "guestNetEnable", "security", "vlanEnable",
                              "mloEnable", "pmfMode", "enable11r") if k in d}
    body["name"] = a.new_name
    body["deviceType"] = d.get("deviceType", 1)
    body["hidePwd"] = bool(d.get("hidePwd", False))
    for k in ("pskSetting", "prohibitWifiShare", "enhancedIotConnectivity", "autoWanAccess"):
        if d.get(k) is not None:
            body[k] = d[k]
    if d.get("vlanEnable"):
        for k in ("vlanId", "vlanSetting"):
            if d.get(k) is not None:
                body[k] = d[k]
    if body.get("security") == 3:
        key = os.environ.get("OMADA_SSID_PASSWORD")
        if not key:
            sys.exit("the new SSID needs a password: re-run with OMADA_SSID_PASSWORD=<password> in the environment")
        body.setdefault("pskSetting", {})["securityKey"] = key
    if a.groups:
        body["apGroupIds"] = [o.ap_group(n.strip())["id"] for n in a.groups.split(",") if n.strip()]
    else:
        bound = o.api("GET", f"/wireless-network/ssids/{d['ssidId']}/ap-groups")["result"]["apGroups"]
        body["apGroupIds"] = [g["id"] for g in bound]
    write(o, a, "POST", f"/wireless-network/wlans/{d['wlanId']}/ssids", body,
          f"{a.new_name}: created from {a.ssid} (bands {bands_str(body['band'])}, guest {body.get('guestNetEnable')}, "
          f"groups {a.groups or 'as source'})")


def cmd_ap_groups(o, a):
    aps = o.aps()
    for g in o.ap_groups():
        members = [f"{x.get('name') or x['mac']} ({x['mac']})" for x in aps if x.get("apGroupId") == g["id"]]
        print(f"{g['name']}{' (default)' if g.get('primary') else ''}: {len(members)} AP(s)")
        for m in members:
            print(f"    {m}")
        print(f"    SSIDs: {', '.join(g.get('ssidNameList') or []) or 'none'}")


def cmd_ap_group(o, a):
    if a.action == "create":
        write(o, a, "POST", "/ap-groups", {"name": a.name, "apMacs": []}, f"AP group {a.name} created (empty)")
    elif a.action == "move":
        a.ap or sys.exit("ap-group move needs --ap <mac>")
        mac = a.ap.upper().replace(":", "-")
        g = o.ap_group(a.name)
        known = {x["mac"]: x.get("apGroupName") for x in o.aps()}
        mac in known or sys.exit(f"no AP with MAC {mac} (have: {', '.join(known)})")
        write(o, a, "PATCH", f"/ap-groups/{g['id']}", {"name": a.name, "addApMacs": [mac], "removeApMacs": []},
              f"AP {mac}: group {known[mac]} -> {a.name}")
    elif a.action == "bind":
        a.groups or sys.exit("ap-group bind needs --groups <group>[,<group>]")
        d = o.ssid(a.name)
        ids = [o.ap_group(n.strip())["id"] for n in a.groups.split(",") if n.strip()]
        write(o, a, "PATCH", f"/wireless-network/ssids/{d['ssidId']}/ap-groups", {"apGroupIds": ids},
              f"{a.name}: broadcast by AP group(s) {a.groups}")


def parse_span(text):
    """'sun-thu 22:00-05:30' -> list of (dayType, sh, sm, eh, em); a span over midnight is split."""
    try:
        days, times = text.split()
        start, end = times.split("-")
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except ValueError:
        sys.exit(f"span must look like 'sun-thu 22:00-05:30' (got {text!r})")
    for m in (sm, em):
        if m not in (0, 15, 30, 45):
            sys.exit("minutes must be :00, :15, :30 or :45")
    if "-" in days:
        d1, d2 = days.split("-")
        i1, i2 = DAYS.index(d1), DAYS.index(d2)
        idx = list(range(i1, i2 + 1)) if i1 <= i2 else list(range(i1, 7)) + list(range(0, i2 + 1))
    elif days == "all":
        idx = list(range(7))
    else:
        idx = [DAYS.index(days)]
    segs = []
    for i in idx:
        if (eh, em) > (sh, sm):
            segs.append((i + 1, sh, sm, eh, em))
        else:   # crosses midnight: tonight until 24:00, then the next day from 00:00
            segs.append((i + 1, sh, sm, 24, 0))
            segs.append(((i + 1) % 7 + 1, 0, 0, eh, em))
    return segs


def cmd_time_range(o, a):
    if a.action == "delete":
        tid = o.time_range_id(a.name)
        write(o, a, "DELETE", f"/time-range-profile/{tid}", None, f"time range {a.name} deleted")
        return
    if not a.span:
        sys.exit("give at least one --span")
    if all(sp.split()[0] == "all" for sp in a.span):
        # Every-day mode: one entry per period instead of one per day per period.
        segs = sorted({(0, sh, sm, eh, em) for sp in a.span for _, sh, sm, eh, em in parse_span(sp)})
        body = {"name": a.name, "dayMode": 0,
                "timeList": [{"dayType": 0, "startTimeH": sh, "startTimeM": sm, "endTimeH": eh, "endTimeM": em} for _, sh, sm, eh, em in segs]}
    else:
        segs = []
        for sp in a.span:
            segs.extend(parse_span(sp))
        segs.sort()
        body = {"name": a.name, "dayMode": 3,
                "customDayMode": {f"day{d.capitalize()}": any(s[0] == i + 1 for s in segs) for i, d in enumerate(DAYS)},
                "timeList": [{"dayType": d, "startTimeH": sh, "startTimeM": sm, "endTimeH": eh, "endTimeM": em} for d, sh, sm, eh, em in segs]}
    write(o, a, "POST", "/time-range-profiles", body, f"time range {a.name} created with {len(segs)} entries")


def cmd_mac_group(o, a):
    if a.action == "delete":
        g = o.mac_group(a.name) or sys.exit(f"no MAC group named {a.name!r}")
        write(o, a, "DELETE", f"/profiles/groups/2/{g.get('groupId') or g.get('id')}", None, f"MAC group {a.name} deleted")
        return
    if not a.mac:
        sys.exit("give at least one --mac label=aa:bb:cc:f3:32:a3")
    macs = []
    for item in a.mac:
        label, _, mac = item.partition("=")
        mac = mac.replace(":", "-").upper()
        if len(mac) != 17:
            sys.exit(f"bad MAC in {item!r}")
        macs.append({"name": label or mac, "macAddress": mac})
    body = {"name": a.name, "type": 2, "macAddressList": macs}
    existing = o.mac_group(a.name)
    if a.action == "create":
        if existing:
            sys.exit(f"MAC group {a.name!r} exists; use 'set' to replace its members")
        write(o, a, "POST", "/profiles/groups", body, f"MAC group {a.name} created with {len(macs)} addresses")
    else:
        if not existing:
            sys.exit(f"MAC group {a.name!r} does not exist; use 'create'")
        write(o, a, "PATCH", f"/profiles/groups/2/{existing.get('groupId') or existing.get('id')}", body,
              f"MAC group {a.name} set to {len(macs)} addresses")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="print the request instead of sending it")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list, write=False)
    s = sub.add_parser("show"); s.add_argument("ssid"); s.set_defaults(fn=cmd_show, write=False)
    sub.add_parser("profiles").set_defaults(fn=cmd_profiles, write=False)
    s = sub.add_parser("set"); s.add_argument("ssid"); s.add_argument("--bands"); s.add_argument("--guest", choices=["on", "off"])
    s.add_argument("--broadcast", choices=["on", "off"]); s.set_defaults(fn=cmd_set, write=True)
    s = sub.add_parser("schedule"); s.add_argument("ssid"); s.add_argument("time_range")
    s.add_argument("--mode", choices=["on", "off"], default="on", help="radio on during the range (default) or off during it")
    s.set_defaults(fn=cmd_schedule, write=True)
    s = sub.add_parser("mac-filter"); s.add_argument("ssid"); s.add_argument("policy", choices=["deny", "allow", "off"])
    s.add_argument("group", nargs="?"); s.set_defaults(fn=cmd_mac_filter, write=True)
    s = sub.add_parser("time-range"); s.add_argument("action", choices=["create", "delete"]); s.add_argument("name")
    s.add_argument("--span", action="append"); s.set_defaults(fn=cmd_time_range, write=True)
    s = sub.add_parser("mac-group"); s.add_argument("action", choices=["create", "set", "delete"]); s.add_argument("name")
    s.add_argument("--mac", action="append"); s.set_defaults(fn=cmd_mac_group, write=True)
    s = sub.add_parser("clone"); s.add_argument("ssid"); s.add_argument("new_name"); s.add_argument("--groups")
    s.set_defaults(fn=cmd_clone, write=True)
    sub.add_parser("ap-groups").set_defaults(fn=cmd_ap_groups, write=False)
    s = sub.add_parser("ap-group"); s.add_argument("action", choices=["create", "move", "bind"]); s.add_argument("name")
    s.add_argument("--ap", help="AP MAC, for move"); s.add_argument("--groups", help="comma-separated group names, for bind")
    s.set_defaults(fn=cmd_ap_group, write=True)
    a = p.parse_args()
    if getattr(a, "policy", None) in ("deny", "allow") and not a.group:
        p.error("mac-filter deny|allow needs a group name")
    o = Omada(load_env(), write=a.write and not a.dry_run)
    a.fn(o, a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
