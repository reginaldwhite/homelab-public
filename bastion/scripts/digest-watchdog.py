#!/usr/bin/env python3
"""digest-watchdog.py — notice when the health digester has gone quiet.

    digest-watchdog.py            check, and tag the owner if the digest is stale
    digest-watchdog.py --test     post a sample alert, to prove delivery works
    digest-watchdog.py --dry-run  say what it would do, send nothing

The digester runs on forge and decides what reaches a human. That makes the whole
alerting path depend on forge, which is the *least* available host here: off
11pm-5am by design, and down for two months in mid-2026 after a power event.

A digester that has stopped cannot report that it has stopped, and silence is not
an alert. So bastion — always on, and the host that would otherwise have nothing
to say — checks once a day that a digest actually appeared, and tags the owner if
one hasn't. This is the only machine-to-human tag left in the system apart from
the two catastrophe conditions in health-report.py.

Without this, a forge outage silently ends all alerting: the exact failure that
went unnoticed from May to September 2026, moved one layer up and made harder to
see.

Needs read access to #homelab-status, so bastion keeps SLACK_BOT_TOKEN even if
reports later move to a post-only webhook. Credentials come from ../.env.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

STATUS_CHANNEL = "C0C3MAXLSRK"    # #homelab-status
LOGS_CHANNEL = "C0C3XSNE71T"      # #homelab-logs
OWNER_MENTION = "<@U01E91JBPPG>"  # robbywhite
DIGEST_PREFIX = ":clipboard: homelab digest"   # set by forge/scripts/health-digest.py

MAX_DIGEST_AGE_S = 26 * 3600   # daily digest at 07:30, so 26h tolerates a late one
LOG = "/home/robby/logs/digest-watchdog.log"


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now().astimezone().isoformat()} [digest-watchdog] {msg}\n")


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


def slack(method, **params):
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("no SLACK_BOT_TOKEN — the watchdog needs read access")
    url = f"https://slack.com/api/{method}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def post(text):
    hook = os.environ.get("SLACK_STATUS_WEBHOOK_URL", "")
    try:
        if hook:
            req = urllib.request.Request(
                hook, data=json.dumps({"text": text}).encode(),
                headers={"Content-type": "application/json"})
            return urllib.request.urlopen(req, timeout=20).read().decode().strip() == "ok"
        token = os.environ.get("SLACK_BOT_TOKEN", "")
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=json.dumps({"channel": STATUS_CHANNEL, "text": text}).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-type": "application/json; charset=utf-8"})
        return bool(json.load(urllib.request.urlopen(req, timeout=20)).get("ok"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log(f"ERROR: could not post: {exc}")
        return False


def last_digest_age():
    """Seconds since the newest digest in #homelab-status, or None if there is none."""
    body = slack("conversations.history", channel=STATUS_CHANNEL, limit=100)
    if not body.get("ok"):
        raise RuntimeError(f"conversations.history failed: {body.get('error')}")
    for msg in body.get("messages", []):        # newest first
        if msg.get("text", "").startswith(DIGEST_PREFIX):
            return time.time() - float(msg["ts"])
    return None


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    load_env()

    if arg == "--test":
        log("test alert requested")
        ok = post(f"{OWNER_MENTION} :test_tube: digest watchdog delivery test — this is "
                  "what you would see if the health digester on forge stopped posting. "
                  "Nothing is wrong.")
        return 0 if ok else 1

    try:
        age = last_digest_age()
    except (RuntimeError, urllib.error.URLError, OSError, ValueError) as exc:
        # A watchdog that cannot look is not a clean bill of health, so it says so.
        log(f"ERROR: could not check: {exc}")
        text = (f"{OWNER_MENTION} :x: digest watchdog could not check whether the health "
                f"digester is running: {exc}")
        if arg == "--dry-run":
            print(text)
            return 1
        post(text)
        return 1

    if age is None:
        msg = "no digest has ever been posted"
    elif age > MAX_DIGEST_AGE_S:
        msg = f"the last digest was {int(age / 3600)}h ago"
    else:
        log(f"ok: last digest {int(age / 60)}m ago")
        if arg == "--dry-run":
            print(f"ok: last digest {int(age / 60)}m ago — nothing to do")
        return 0

    text = (f"{OWNER_MENTION} :warning: {msg} — the health digester on forge does not "
            f"look like it is running, so nothing is being escalated. Raw reports are "
            f"still arriving in <#{LOGS_CHANNEL}>; check `health-digest.log` on forge.")
    log(f"ALERT: {msg}")
    if arg == "--dry-run":
        print(text)
        return 0
    return 0 if post(text) else 1


if __name__ == "__main__":
    sys.exit(main())
