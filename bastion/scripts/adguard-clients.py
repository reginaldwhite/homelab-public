#!/usr/bin/env python3
"""adguard-clients.py — AdGuard's client list and static leases as a file in git.

    adguard-clients.py export [--write]   the controller's clients and static leases as JSON, to
                                          stdout, or --write into ../adguard/clients.json (then
                                          commit it)
    adguard-clients.py diff               what `apply` would change, nothing applied
    adguard-clients.py apply [--prune]    make the controller match ../adguard/clients.json:
                                          add missing clients and leases, update changed ones;
                                          entries only the controller has are REPORTED and kept,
                                          unless --prune removes them

The file is the one place the per-device truth lives (docs/lan-clients.md is the prose over it):
a client's name, the MACs and addresses it answers to, its tags and per-client blocking, and
the static lease that pins each device. AdGuardHome.yaml stays outside git -- it carries the
admin password hash and runtime state -- and this covers the part of it that is configuration.

apply.sh runs `diff` during --validate (when a .env is beside it) and `apply` on every deploy,
after the containers are up, so editing the file and deploying is how a device gets a name or an
address. kids-hours.py keeps writing the children's rules itself; those are not in this file.

Credentials: ADGUARD_USER / ADGUARD_PASSWORD from ../.env, the same the stats poller uses.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(HERE), ".env")
FILE = os.path.join(os.path.dirname(HERE), "adguard", "clients.json")
ADGUARD = "http://127.0.0.1:8888"

# The fields of a client record that are configuration. Anything else AdGuard returns (there is
# nothing else today) is dropped on export so the file does not churn on upgrades.
CLIENT_KEYS = ("name", "ids", "tags", "use_global_settings", "filtering_enabled", "parental_enabled",
               "safebrowsing_enabled", "safe_search", "use_global_blocked_services", "blocked_services",
               "blocked_services_schedule", "upstreams", "ignore_querylog", "ignore_statistics",
               "upstreams_cache_enabled", "upstreams_cache_size")


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


class AdGuard:
    def __init__(self, env):
        user, pw = env.get("ADGUARD_USER"), env.get("ADGUARD_PASSWORD")
        if not (user and pw):
            sys.exit(f"no ADGUARD_USER / ADGUARD_PASSWORD in {ENV_FILE}")
        self.auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    def req(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(ADGUARD + path, data=data, method="POST" if data is not None else "GET",
                                   headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                text = resp.read().decode()
        except urllib.error.HTTPError as e:
            sys.exit(f"{path}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}")
        return json.loads(text) if text.strip().startswith(("{", "[")) else text

    def clients(self):
        return {c["name"]: {k: c[k] for k in CLIENT_KEYS if k in c} for c in self.req("/control/clients").get("clients") or []}

    def leases(self):
        return {l["mac"].lower(): {"mac": l["mac"].lower(), "ip": l["ip"], "hostname": l.get("hostname", "")}
                for l in self.req("/control/dhcp/status").get("static_leases") or []}


def read_file():
    try:
        with open(FILE) as fh:
            d = json.load(fh)
    except OSError:
        sys.exit(f"{FILE} does not exist; create it with `export --write`")
    clients = {c["name"]: {k: c[k] for k in CLIENT_KEYS if k in c} for c in d.get("clients") or []}
    leases = {l["mac"].lower(): {"mac": l["mac"].lower(), "ip": l["ip"], "hostname": l.get("hostname", "")}
              for l in d.get("static_leases") or []}
    return clients, leases


def render(clients, leases):
    return json.dumps({
        "_comment": "AdGuard clients and static leases, applied by scripts/adguard-clients.py on deploy. "
                    "Edit here, then ./deploy.sh bastion. `export --write` refreshes it from the controller.",
        "clients": [clients[n] for n in sorted(clients, key=str.lower)],
        "static_leases": sorted(leases.values(), key=lambda l: tuple(int(x) for x in l["ip"].split("."))),
    }, indent=1, sort_keys=False) + "\n"


def plan(ag, want_clients, want_leases):
    """The list of (kind, action, name, detail) that would make the controller match the file."""
    have_c, have_l = ag.clients(), ag.leases()
    steps = []
    for name, c in want_clients.items():
        if name not in have_c:
            steps.append(("client", "add", name, f"ids {c.get('ids')}"))
        elif have_c[name] != c:
            changed = [k for k in CLIENT_KEYS if have_c[name].get(k) != c.get(k)]
            steps.append(("client", "update", name, ", ".join(f"{k}: {have_c[name].get(k)!r} -> {c.get(k)!r}" for k in changed)))
    for name in have_c:
        if name not in want_clients:
            steps.append(("client", "extra", name, f"only on the controller (ids {have_c[name].get('ids')})"))
    for mac, l in want_leases.items():
        if mac not in have_l:
            steps.append(("lease", "add", mac, f"{l['ip']} {l['hostname']}"))
        elif have_l[mac] != l:
            steps.append(("lease", "replace", mac, f"{have_l[mac]['ip']} {have_l[mac]['hostname']} -> {l['ip']} {l['hostname']}"))
    for mac in have_l:
        if mac not in want_leases:
            steps.append(("lease", "extra", mac, f"only on the controller ({have_l[mac]['ip']} {have_l[mac]['hostname']})"))
    return steps, have_l


def cmd_export(ag, a):
    text = render(ag.clients(), ag.leases())
    if a.write:
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        with open(FILE, "w") as fh:
            fh.write(text)
        print(f"wrote {FILE}")
    else:
        sys.stdout.write(text)


def cmd_diff(ag, a):
    want_c, want_l = read_file()
    steps, _ = plan(ag, want_c, want_l)
    if not steps:
        print("adguard: controller matches clients.json")
        return 0
    for kind, action, name, detail in steps:
        print(f"{kind} {action:7s} {name}: {detail}")
    return 0


def cmd_apply(ag, a):
    want_c, want_l = read_file()
    steps, have_l = plan(ag, want_c, want_l)
    done = 0
    for kind, action, name, detail in steps:
        if action == "extra":
            if not a.prune:
                print(f"kept  {kind} {name} ({detail})")
                continue
            if kind == "client":
                ag.req("/control/clients/delete", {"name": name})
            else:
                ag.req("/control/dhcp/remove_static_lease", have_l[name])
            print(f"removed {kind} {name}")
        elif kind == "client" and action == "add":
            ag.req("/control/clients/add", want_c[name]); print(f"added client {name}")
        elif kind == "client":
            ag.req("/control/clients/update", {"name": name, "data": want_c[name]}); print(f"updated client {name}: {detail}")
        elif action == "add":
            ag.req("/control/dhcp/add_static_lease", want_l[name]); print(f"added lease {name} -> {detail}")
        else:
            ag.req("/control/dhcp/remove_static_lease", have_l[name])
            ag.req("/control/dhcp/add_static_lease", want_l[name]); print(f"replaced lease {name}: {detail}")
        done += 1
    print(f"adguard: {done} change(s) applied" if done else "adguard: controller matches clients.json")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("export"); s.add_argument("--write", action="store_true"); s.set_defaults(fn=cmd_export)
    sub.add_parser("diff").set_defaults(fn=cmd_diff)
    s = sub.add_parser("apply"); s.add_argument("--prune", action="store_true"); s.set_defaults(fn=cmd_apply)
    a = p.parse_args()
    return a.fn(AdGuard(load_env()), a)


if __name__ == "__main__":
    sys.exit(main())
