#!/usr/bin/env python3
"""vault-report.py — report on vault, from bastion, after the weekly backup window.

    vault-report.py            check vault and post the report
    vault-report.py --dry-run  print what would be posted, send nothing

vault is the one host that does not report for itself, and deliberately so. Three
facts make a reporter *on* vault a bad idea, all verified on the machine:

    * its root filesystem is RAM, so nothing installed there survives a reboot —
      and it reboots weekly when the smart plug cycles
    * its flash is vfat mounted fmask=0177, so a script there can never carry an
      executable bit
    * it has no python3 at all

But the decisive reason is not any of those. A reporter on vault could not report
the failure that actually matters: **vault not powering on.** Run from bastion,
which is always on, "vault never came up" becomes a report with status=error
instead of silence.

Scheduled for Sunday 3:30am, after backup-to-tower.sh (2:30) and sync-forge.sh
(3:00) have run and before the smart plug cuts power at 5:00 — so it reports on
the state the backups actually left behind, which is the thing nobody could see
before.

Credentials come from ../.env. See bastion/.env.example.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HOST = "vault"
VAULT_SSH = "root@192.168.0.212"
SSH_KEY = "/home/robby/.ssh/id_ed25519"
LOG = "/home/robby/logs/vault-report.log"

LOGS_CHANNEL = "C0C3XSNE71T"     # #homelab-logs
BOT_MENTION = "<@U0AJY4AETS7>"   # openclaw

BACKUP_STALE_DAYS = 8
FS_WARN_PCT = 80

# One round trip: vault is only reachable for about three hours a week.
PROBE = r"""
grep -E '^(mdState|fsState|mdNumDisabled|mdNumInvalid|mdNumMissing|sbSyncErrs|sbSyncExit)=' \
    /var/local/emhttp/var.ini
echo '---disks---'
grep -E '^(name|status)=' /var/local/emhttp/disks.ini
echo '---space---'
df --output=pcent /mnt/user | tail -1
echo '---backups---'
for d in vaultwarden forge; do
    t=$(find /mnt/user/backups/$d -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1)
    echo "$d=${t:-none}"
done
"""


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now().astimezone().isoformat()} [vault-report] {msg}\n")


def load_env():
    here = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(os.path.dirname(here), ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def post(text):
    hook = os.environ.get("SLACK_LOGS_WEBHOOK_URL", "")
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    try:
        if hook:
            req = urllib.request.Request(
                hook, data=json.dumps({"text": text}).encode(),
                headers={"Content-type": "application/json"})
            if urllib.request.urlopen(req, timeout=20).read().decode().strip() == "ok":
                log("posted via webhook")
                return True
            log("ERROR: webhook rejected the post")
            return False
        if token:
            req = urllib.request.Request(
                "https://slack.com/api/chat.postMessage",
                data=json.dumps({"channel": LOGS_CHANNEL, "text": text}).encode(),
                headers={"Authorization": f"Bearer {token}",
                         "Content-type": "application/json; charset=utf-8"})
            body = json.load(urllib.request.urlopen(req, timeout=20))
            if body.get("ok"):
                log("posted via chat.postMessage")
                return True
            log(f"ERROR: Slack rejected the post: {json.dumps(body)[:200]}")
            return False
        log("ERROR: no SLACK_LOGS_WEBHOOK_URL and no SLACK_BOT_TOKEN")
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log(f"ERROR: posting failed: {exc}")
        return False


def probe():
    """Returns (ok, text). ok is False when vault could not be reached at all."""
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-i", SSH_KEY, VAULT_SSH, PROBE],
            capture_output=True, text=True, timeout=180)
    except subprocess.SubprocessError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:400]
    return True, proc.stdout


def parse(out):
    kv, disks, space, backups = {}, [], None, {}
    section = "var"
    name = None
    for line in out.splitlines():
        line = line.strip()
        if line == "---disks---":
            section = "disks"; continue
        if line == "---space---":
            section = "space"; continue
        if line == "---backups---":
            section = "backups"; continue
        if not line:
            continue
        if section == "var" and "=" in line:
            key, value = line.split("=", 1)
            kv[key] = value.strip().strip('"')
        elif section == "disks" and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip().strip('"')
            if key == "name":
                name = value
            elif key == "status" and name:
                disks.append((name, value))
        elif section == "space":
            space = int(line.replace("%", "").strip())
        elif section == "backups" and "=" in line:
            key, value = line.split("=", 1)
            backups[key] = None if value == "none" else float(value)
    return kv, disks, space, backups


def build(kv, disks, space, backups, now):
    findings = []

    if kv.get("mdState") != "STARTED":
        findings.append({"check": "array",
                         "detail": f"mdState is {kv.get('mdState')!r}, expected STARTED"})
    if kv.get("fsState") != "Started":
        findings.append({"check": "array filesystem",
                         "detail": f"fsState is {kv.get('fsState')!r}, expected Started"})
    for key, label in (("mdNumDisabled", "disabled"), ("mdNumInvalid", "invalid"),
                       ("mdNumMissing", "missing")):
        if kv.get(key, "0") != "0":
            findings.append({"check": "array disks",
                             "detail": f"{kv[key]} {label} disk(s)"})
    if kv.get("sbSyncErrs", "0") != "0":
        findings.append({"check": "parity",
                         "detail": f"last parity check found {kv['sbSyncErrs']} error(s)"})
    if kv.get("sbSyncExit", "0") != "0":
        findings.append({"check": "parity",
                         "detail": f"last parity check exited {kv['sbSyncExit']}"})

    bad = [f"{n} is {s}" for n, s in disks if s != "DISK_OK"]
    if bad:
        findings.append({"check": "disk status", "detail": ", ".join(bad)})

    if space is not None and space >= FS_WARN_PCT:
        findings.append({"check": "array space", "detail": f"/mnt/user is {space}% full"})

    ages = {}
    for source, mtime in backups.items():
        if mtime is None:
            findings.append({"check": f"backup: {source}",
                             "detail": f"nothing has ever been backed up to /mnt/user/backups/{source}"})
            continue
        days = round((now - mtime) / 86400, 1)
        ages[f"{source}_backup_age_days"] = days
        if days > BACKUP_STALE_DAYS:
            findings.append({"check": f"backup: {source}",
                             "detail": f"newest file is {days} days old, expected weekly"})

    return {
        "host": HOST,
        "kind": "health",
        "status": "warn" if findings else "ok",
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "findings": findings,
        "metrics": dict({"fs_max_pct": space,
                         "backup_age_days": max(ages.values()) if ages else None,
                         "disks": len(disks)}, **ages),
    }


def report_text(report):
    n = len(report["findings"])
    head = (f"{BOT_MENTION} {report['host']} {report['kind']}: {report['status']} "
            f"({n} finding{'' if n == 1 else 's'})")
    return head + "\n```json\n" + json.dumps(report, indent=1) + "\n```"


def main():
    dry = "--dry-run" in sys.argv[1:]
    load_env()
    now = time.time()

    ok, out = probe()
    if not ok:
        # The failure a reporter on vault could never report.
        report = {
            "host": HOST, "kind": "health", "status": "error",
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "findings": [{"check": "reachability",
                          "detail": "vault did not answer during its backup window — "
                                    f"it may not have powered on. {out[:300]}"}],
            "metrics": {},
        }
        log(f"ERROR: unreachable: {out[:200]}")
        if dry:
            print(report_text(report))
            return 1
        post(report_text(report))
        return 1

    report = build(*parse(out), now)
    text = report_text(report)
    if dry:
        print(text)
        return 0
    log(f"reported status={report['status']} findings={len(report['findings'])}")
    return 0 if post(text) else 1


if __name__ == "__main__":
    sys.exit(main())
