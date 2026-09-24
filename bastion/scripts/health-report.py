#!/usr/bin/env python3
"""health-report.py — bastion reports its own health as a structured message.

    health-report.py            run the checks and post the report
    health-report.py --test     post a sample report, to prove delivery works
    health-report.py --dry-run  print what would be posted, send nothing

Posts a machine-readable report to #homelab-logs and tags the OpenClaw bot. It
deliberately does NOT decide whether anything is urgent: that judgement moved to
the digester on forge, which can correlate across hosts and across days, which no
single reporter can do. This script's job is to state facts accurately.

Two exceptions bypass the digester and tag the owner directly in #homelab-status,
because they cannot wait for forge to wake up (it is off 11pm-5am):

    * any filesystem at 95% or more
    * the weekly backup to vault failed on its last run

Both are deterministic, both are rare, and both are things you want to know about
at 3am rather than at 7:30. Everything else goes to the digester.

Why bastion reports at all, rather than being polled: the other monitor runs on
forge, which is down 11pm-5am by design and was down for two months in mid-2026.
A check that only runs while forge is up cannot speak for bastion overnight.

Credentials come from ../.env on the deployed copy, which deploy.sh never pushes
and never overwrites. See bastion/.env.example.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HOST = "bastion"
HEALTH_CMD = "/home/robby/bin/openclaw-cmd"
EXPECTED_SECTIONS = 5
LOG = "/home/robby/logs/health-report.log"

LOGS_CHANNEL = "C0C3XSNE71T"     # #homelab-logs — raw reports, read by the digester
STATUS_CHANNEL = "C0C3MAXLSRK"   # #homelab-status — only things meant for a human
BOT_MENTION = "<@U0AJY4AETS7>"   # openclaw
OWNER_MENTION = "<@U01E91JBPPG>"  # robbywhite

FS_CRITICAL_PCT = 95


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now().astimezone().isoformat()} [health-report] {msg}\n")


def load_env():
    """Read ../.env, but let an already-exported value win.

    Matches scripts/git-credential-homelab, and is what makes this testable: a
    one-off run can be pointed elsewhere without editing the deployed .env.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(os.path.dirname(here), ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def post(channel, text, webhook_var):
    """Post to Slack. A webhook is preferred: it can only post, to one channel."""
    hook = os.environ.get(webhook_var, "")
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    try:
        if hook:
            req = urllib.request.Request(
                hook, data=json.dumps({"text": text}).encode(),
                headers={"Content-type": "application/json"})
            body = urllib.request.urlopen(req, timeout=20).read().decode()
            if body.strip() == "ok":
                log(f"posted to {channel} via webhook")
                return True
            log(f"ERROR: webhook rejected the post: {body[:200]}")
            return False
        if token:
            req = urllib.request.Request(
                "https://slack.com/api/chat.postMessage",
                data=json.dumps({"channel": channel, "text": text}).encode(),
                headers={"Authorization": f"Bearer {token}",
                         "Content-type": "application/json; charset=utf-8"})
            body = json.load(urllib.request.urlopen(req, timeout=20))
            if body.get("ok"):
                log(f"posted to {channel} via chat.postMessage")
                return True
            log(f"ERROR: Slack rejected the post: {json.dumps(body)[:200]}")
            return False
        log(f"ERROR: no {webhook_var} and no SLACK_BOT_TOKEN — cannot post")
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # A report nobody receives is not a report, so a failed post is recorded
        # rather than swallowed.
        log(f"ERROR: posting to {channel} failed: {exc}")
        return False


def run_health():
    """Run the same `health` verb the agent's monitor runs.

    Reusing it means the two can never disagree about what healthy means. It is a
    forced-command wrapper, so it wants the verb in SSH_ORIGINAL_COMMAND as well
    as in argv.
    """
    env = dict(os.environ, SSH_ORIGINAL_COMMAND="health")
    proc = subprocess.run([HEALTH_CMD, "health"], capture_output=True, text=True,
                          env=env, timeout=300)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def parse_sections(out):
    """Split `== name` blocks into {name: body}, preserving order."""
    sections, name = {}, None
    for line in out.splitlines():
        if line.startswith("== "):
            name = line[3:].strip()
            sections[name] = []
        elif name is not None:
            sections[name].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def filesystem_max_pct():
    """Highest use% across real filesystems, as a number worth trending.

    The digester can only spot "climbing 3% a day" if the number is in the report
    rather than buried in prose, so it is reported even when nothing is wrong.
    """
    try:
        # No -P here: GNU df rejects it alongside --output.
        proc = subprocess.run(
            ["df", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs",
             "-x", "overlay", "--output=pcent"],
            capture_output=True, text=True, timeout=30)
        pcts = [int(m.group(1)) for m in
                (re.match(r"\s*(\d+)%", line) for line in proc.stdout.splitlines()[1:])
                if m]
        return max(pcts) if pcts else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def build_report(sections, fs_pct):
    findings = [{"check": name, "detail": body}
                for name, body in sections.items() if body != "none"]

    if any("CHECK FAILED" in f["detail"] for f in findings):
        status = "error"     # a check could not run; never an all clear
    elif findings:
        status = "warn"
    else:
        status = "ok"

    def count(name):
        body = sections.get(name, "none")
        return 0 if body == "none" else len(body.splitlines())

    report = {
        "host": HOST,
        "kind": "health",
        "status": status,
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "findings": findings,
        "metrics": {
            "fs_max_pct": fs_pct,
            "journal_errors": count("system journal errors, last 14 hours"),
            "stopped_containers": count("stopped containers"),
        },
    }
    return report


def report_text(report):
    n = len(report["findings"])
    head = (f"{BOT_MENTION} {report['host']} {report['kind']}: {report['status']} "
            f"({n} finding{'' if n == 1 else 's'})")
    return head + "\n```json\n" + json.dumps(report, indent=1) + "\n```"


def critical_reasons(report):
    """The only conditions allowed to skip the digester and wake a human."""
    reasons = []
    fs = report["metrics"].get("fs_max_pct")
    if fs is not None and fs >= FS_CRITICAL_PCT:
        reasons.append(f"a filesystem is at {fs}%")
    for finding in report["findings"]:
        if "backup" in finding["check"] and "FAILED" in finding["detail"]:
            reasons.append("the weekly backup to vault failed on its last run")
    return reasons


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    load_env()

    if arg == "--test":
        log("test report requested")
        sample = {"host": HOST, "kind": "health", "status": "ok",
                  "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                  "findings": [], "metrics": {"fs_max_pct": 0}}
        ok = post(LOGS_CHANNEL,
                  report_text(sample) + "\n_(delivery test — not a real report)_",
                  "SLACK_LOGS_WEBHOOK_URL")
        return 0 if ok else 1

    dry = arg == "--dry-run"

    if not os.access(HEALTH_CMD, os.X_OK):
        log(f"ERROR: {HEALTH_CMD} is missing or not executable")
        report = {"host": HOST, "kind": "health", "status": "error",
                  "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                  "findings": [{"check": "health verb",
                                "detail": f"{HEALTH_CMD} missing on {HOST}"}],
                  "metrics": {}}
        if not dry:
            post(LOGS_CHANNEL, report_text(report), "SLACK_LOGS_WEBHOOK_URL")
        else:
            print(report_text(report))
        return 1

    rc, out = run_health()
    sections = parse_sections(out)

    # A truncated run must never read as healthy.
    if rc != 0 or len(sections) != EXPECTED_SECTIONS:
        detail = (f"health verb exited {rc}, {len(sections)} of {EXPECTED_SECTIONS} "
                  f"sections\n{out[:1200]}")
        report = {"host": HOST, "kind": "health", "status": "error",
                  "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                  "findings": [{"check": "health verb", "detail": detail}],
                  "metrics": {}}
        log(f"ERROR: rc={rc} sections={len(sections)}")
        if dry:
            print(report_text(report))
            return 1
        post(LOGS_CHANNEL, report_text(report), "SLACK_LOGS_WEBHOOK_URL")
        return 1

    report = build_report(sections, filesystem_max_pct())
    text = report_text(report)

    if dry:
        print(text)
        for reason in critical_reasons(report):
            print(f"\n[would also escalate to #homelab-status] {reason}")
        return 0

    ok = post(LOGS_CHANNEL, text, "SLACK_LOGS_WEBHOOK_URL")
    log(f"reported status={report['status']} findings={len(report['findings'])}")

    # The bypass. Deliberately narrow: see the module docstring.
    reasons = critical_reasons(report)
    if reasons:
        log(f"CRITICAL bypass: {'; '.join(reasons)}")
        post(STATUS_CHANNEL,
             f"{OWNER_MENTION} :rotating_light: {HOST}: " + "; ".join(reasons) +
             ".\nNot waiting for the digester. Full report is in <#" +
             LOGS_CHANNEL + ">.",
             "SLACK_STATUS_WEBHOOK_URL")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
