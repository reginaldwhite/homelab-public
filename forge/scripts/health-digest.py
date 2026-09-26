#!/usr/bin/env python3
"""health-digest.py — read the hosts' raw reports, decide what deserves a human.

    health-digest.py --triage    every 10 min; posts only when something escalates
    health-digest.py --digest    daily; always posts, so silence is itself a signal
    health-digest.py --dry-run   print what would be posted, send nothing
                                 (combine with --triage or --digest)

Hosts post structured reports to #homelab-logs and state facts only. This reads
them and decides what reaches #homelab-status, so severity is judged in one place
that can correlate across hosts and across days rather than by each reporter
alone.

Division of labour, deliberately:

    this script   what counts as serious, what gets posted, where the cursor is
    the agent     the prose summary, correlation across days, a suggested action

The serious categories are decided by the rules below, never by the model, so a
bad summary degrades to "bland or noisy" rather than "missed outage". Every
escalation carries the raw finding underneath it, so a wrong summary is visible
rather than authoritative.

Why it polls rather than listening for mentions: Slack Socket Mode has no replay,
and forge is powered off 11pm-5am, so any event posted overnight is delivered to
nobody and never retried. A cursor picks those reports up the next morning.

Why it is not an OpenClaw cron job: OpenClaw cron only schedules `agentTurn`
payloads, so putting the worker there would mean the model decides what counts as
an outage. The rules below stay deterministic; the model only writes the prose.

Run from the deployed copy by forge/crontab. The cursor and last-seen map live in
STATE, outside the repo and outside the agent's state directory.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

LOGS_CHANNEL = "C0C3XSNE71T"      # #homelab-logs — the hosts' raw reports
STATUS_CHANNEL = "C0C3MAXLSRK"    # #homelab-status — only things meant for a human
OWNER_MENTION = "<@U01E91JBPPG>"  # robbywhite
DIGEST_PREFIX = ":clipboard: homelab digest"   # bastion's watchdog looks for this

STATE = "/home/robby/logs/health-digest-state.json"
LOG = "/home/robby/logs/health-digest.log"

MAX_LOOKBACK_S = 7 * 86400   # a lost cursor must not replay months
DIGEST_WINDOW_S = 25 * 3600  # the first digest, before it has a cursor of its own
FS_WARN_PCT = 90
BACKUP_STALE_DAYS = 8        # the backup window is weekly; 8 allows one late Sunday
ESCALATION_COOLDOWN_S = 12 * 3600   # do not repeat the same escalation hourly

# triage and the digest keep SEPARATE cursors. They shared one until 2026-09-26,
# and because triage runs far more often it always got there first: by 7:30 the
# digest could only see whatever had arrived since the 7:00 triage, which in
# practice was forge's 7:10 report and nothing else. Every daily digest for days
# read "1 report(s) from forge" while bastion, cinema and vault were reporting
# normally. A digest that silently narrows its own window is worse than no digest,
# because it still looks like a full one.
CURSOR_KEY = {"triage": "cursor", "digest": "cursor_digest"}

# How long a host may stay quiet before its silence is itself the finding.
# cinema is absent from this map on purpose: it is asleep most of the time, so
# absence is normal rather than a fault. It is still named in the roll-call below,
# so "cinema has not been heard from in nine days" is visible without being an
# alert -- the earlier plan left that to an agent cron job that has since been
# retired, which meant nothing watched cinema at all.
EXPECTED_SILENCE_S = {
    "bastion": 26 * 3600,
    "forge": 26 * 3600,
    "vault": 8 * 86400,
}

# Named in every daily digest whether they reported or not. Without this the
# digest can only list hosts that happened to speak, which reads as "the others
# are fine" when it means "I did not look at them".
ROLLCALL = ("bastion", "forge", "cinema", "vault")
ROLLCALL_NOTE = {"cinema": "asleep is normal"}


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(f"{datetime.now().astimezone().isoformat()} [health-digest] {msg}\n")


def openclaw(*args, timeout=300):
    """Run an openclaw CLI command inside the container."""
    proc = subprocess.run(["docker", "exec", "openclaw", "node", "openclaw.mjs", *args],
                          capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def load_state():
    try:
        with open(STATE) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    state.setdefault("cursor", None)
    state.setdefault("cursor_digest", None)
    state.setdefault("last_seen", {})
    state.setdefault("escalated", {})
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, STATE)


def read_reports(cursor):
    """Fetch new messages from #homelab-logs and keep the ones that are reports.

    cursor is a Slack ts string; everything at or before it is skipped. Callers
    pass a synthetic one to open a fixed window (see main()).
    """
    args = ["message", "read", "--channel", "slack",
            "--target", f"channel:{LOGS_CHANNEL}", "--limit", "200", "--json"]
    if cursor:
        args += ["--after", cursor]
    rc, out, err = openclaw(*args)
    if rc != 0:
        log(f"ERROR: message read failed rc={rc}: {(err or out)[:300]}")
        return None, cursor

    try:
        messages = json.loads(out)["payload"]["messages"]
    except (ValueError, KeyError, TypeError) as exc:
        log(f"ERROR: could not parse message read output: {exc}")
        return None, cursor

    floor = time.time() - MAX_LOOKBACK_S
    reports, newest = [], cursor
    for msg in sorted(messages, key=lambda m: float(m.get("ts", 0))):
        ts = float(msg.get("ts", 0))
        if cursor and ts <= float(cursor):
            continue
        if ts < floor:
            continue
        if newest is None or ts > float(newest):
            newest = msg["ts"]
        # The language tag is optional: Slack keeps "```json" when a report is posted
        # with chat.postMessage, but drops it when the same text goes through
        # rich-text conversion, which would otherwise make reports invisible here.
        block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", msg.get("text", ""), re.S)
        if not block:
            continue          # not a report: chatter, or a human posting
        try:
            report = json.loads(block.group(1))
        except ValueError:
            reports.append({"host": "?", "kind": "unparsable", "status": "error",
                            "ts": msg.get("ts"), "findings": [
                                {"check": "report format",
                                 "detail": "a report in #homelab-logs is not valid JSON"}],
                            "metrics": {}})
            continue
        if "host" in report and "kind" in report:
            report["_ts"] = ts
            reports.append(report)
    return reports, newest


def escalations(reports, state, now):
    """The deterministic half. Returns [(key, text)] — the model never decides these."""
    found = []

    def add(key, text):
        found.append((key, text))

    for report in reports:
        host = report.get("host", "?")
        findings = report.get("findings") or []

        if report.get("status") == "error":
            detail = findings[0]["detail"].splitlines()[0] if findings else "no detail"
            add(f"{host}:error", f"*{host}*: a check could not run — {detail}")

        metrics = report.get("metrics") or {}

        fs = metrics.get("fs_max_pct")
        if isinstance(fs, int) and fs >= FS_WARN_PCT:
            add(f"{host}:fs", f"*{host}*: a filesystem is at {fs}%")

        # Reported as a number rather than inferred from prose, so vault's backup
        # freshness is judged by the same rule as anyone else's.
        age = metrics.get("backup_age_days")
        if isinstance(age, (int, float)) and age > BACKUP_STALE_DAYS:
            add(f"{host}:backup-age",
                f"*{host}*: newest backup is {age} days old, expected weekly")

        for finding in findings:
            check = (finding.get("check") or "").lower()
            detail = finding.get("detail") or ""
            if "backup" in check:
                if "FAILED" in detail:
                    add(f"{host}:backup-failed",
                        f"*{host}*: the weekly backup to vault failed — {detail.splitlines()[0]}")
                elif "expected weekly" in detail or "never" in detail:
                    add(f"{host}:backup-stale",
                        f"*{host}*: the weekly backup to vault is stale — {detail.splitlines()[0]}")

        if report.get("kind") == "vaultwarden-version" and report.get("status") != "ok":
            add("vaultwarden:version",
                f"*vaultwarden*: {findings[0]['detail'] if findings else 'behind the latest release'}")

    # Silence is a finding too, but only for hosts that are supposed to speak up.
    for host, window in EXPECTED_SILENCE_S.items():
        last = state["last_seen"].get(host)
        if last and now - last > window:
            hours = int((now - last) / 3600)
            add(f"{host}:silent", f"*{host}*: has not reported in {hours}h")

    # Do not repeat the same escalation every hour; the daily digest still carries it.
    fresh = []
    for key, text in found:
        if now - state["escalated"].get(key, 0) > ESCALATION_COOLDOWN_S:
            fresh.append((key, text))
    return found, fresh


def human_age(seconds):
    hours = seconds / 3600
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def rollcall(reports, state, now):
    """One line naming every host that is supposed to report, reported or not."""
    latest = {}
    for report in reports:
        if report.get("kind") != "health":
            continue
        host = report.get("host")
        if host and report.get("_ts", 0) >= latest.get(host, {}).get("_ts", 0):
            latest[host] = report

    bits = []
    for host in ROLLCALL:
        report = latest.get(host)
        if report:
            n = len(report.get("findings") or [])
            bits.append(f"{host} {report.get('status')}" + (f" ({n})" if n else ""))
            continue
        note = ROLLCALL_NOTE.get(host)
        last = state["last_seen"].get(host)
        if not last:
            text = f"{host} never reported"
        else:
            text = f"{host} silent {human_age(now - last)}"
        bits.append(text + (f", {note}" if note else ""))
    return " · ".join(bits)


def compact(reports, limit=1800):
    """Render reports small enough to leave the model room to think.

    The agent's bootstrap already consumes most of a 16k window, so this is kept
    deliberately terse rather than pasting raw reports in.
    """
    lines = []
    for report in reports:
        host = report.get("host", "?")
        metrics = report.get("metrics") or {}
        bits = " ".join(f"{k}={v}" for k, v in metrics.items() if v is not None)
        lines.append(f"- {host}/{report.get('kind')}: {report.get('status')} {bits}".rstrip())
        for finding in (report.get("findings") or []):
            detail = " / ".join(finding.get("detail", "").splitlines())[:220]
            lines.append(f"    {finding.get('check')}: {detail}")
    text = "\n".join(lines)
    return text[:limit] + ("\n…(truncated)" if len(text) > limit else "")


def ask_agent(reports, fresh, mode):
    """Hand the collected facts to the agent for the summary and a suggested action."""
    if not reports and not fresh:
        return None
    prompt = (
        "You are summarising homelab health reports for a Slack channel. "
        "Be concrete and brief: at most 4 sentences, plain text, no headings, no "
        "bullet lists, no preamble. Do not invent hosts or numbers that are not "
        "below, and only speak about hosts that actually appear below — a host "
        "that did not report is not known to be healthy. Do not tag anyone.\n\n"
        f"Reports collected since the last digest ({len(reports)}):\n{compact(reports)}\n\n"
    )
    if fresh:
        prompt += ("These already qualify as problems and will be flagged "
                   "automatically, so do not repeat them verbatim — instead say what "
                   "they most likely mean together, and name ONE action worth taking:\n"
                   + "\n".join(f"- {t}" for _, t in fresh) + "\n\n")
    else:
        prompt += ("None of the rules fired. Open with one sentence confirming "
                   "nothing needs action -- in your own words, do not reuse the "
                   "word \"qualifies\" -- then mention any number worth watching "
                   "before it becomes one.\n\n")
    prompt += "Reply with the summary text only."

    rc, out, err = openclaw("agent", "--session-id", f"health-{mode}-{int(time.time())}",
                            "--message", prompt, "--json", timeout=420)
    if rc != 0:
        log(f"ERROR: agent call failed rc={rc}: {(err or out)[:300]}")
        return None
    try:
        payloads = json.loads(out)["result"]["payloads"]
        text = " ".join(p.get("text", "") for p in payloads).strip()
        return text or None
    except (ValueError, KeyError, TypeError) as exc:
        log(f"ERROR: could not parse agent reply: {exc}")
        return None


def compose(reports, fresh, summary, mode, roll=None):
    hosts = sorted({r.get("host", "?") for r in reports})
    head = (f"{DIGEST_PREFIX} — {len(reports)} report(s)"
            f"{' from ' + ', '.join(hosts) if hosts else ''}")
    body = []
    if roll:
        body.append(roll)
    if fresh:
        body.append(f"{OWNER_MENTION} needs a look:")
        body += [f"• {text}" for _, text in fresh]
    if summary:
        body.append(summary)
    elif not fresh:
        # The agent is allowed to fail without taking the digest down with it.
        body.append("No problems found." if reports else
                    "No reports arrived since the last digest.")
    if mode == "triage":
        # A triage post only happens because something escalated, so lead with it.
        return "\n".join([f"{OWNER_MENTION} :warning: homelab"] +
                         [f"• {text}" for _, text in fresh] +
                         ([summary] if summary else []))
    return "\n".join([head] + body)


def post(text):
    rc, out, err = openclaw("message", "send", "--channel", "slack",
                            "--target", f"channel:{STATUS_CHANNEL}", "--message", text)
    if rc != 0:
        log(f"ERROR: posting to #homelab-status failed rc={rc}: {(err or out)[:300]}")
        return False
    return True


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    mode = "triage" if "--triage" in args else "digest" if "--digest" in args else None
    if mode is None:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("need --triage or --digest", file=sys.stderr)
        return 2

    state = load_state()
    now = time.time()

    key = CURSOR_KEY[mode]
    cursor = state.get(key)
    if cursor is None and mode == "digest":
        # First digest after the cursors were split: cover a day rather than
        # inheriting triage's position, which is minutes old.
        cursor = f"{now - DIGEST_WINDOW_S:.6f}"

    reports, newest = read_reports(cursor)
    if reports is None:
        # Could not read. Say nothing on triage; on the daily digest this is worth
        # reporting, since a digest that cannot read is not an all clear.
        if mode == "digest" and not dry:
            post(f"{DIGEST_PREFIX} — {OWNER_MENTION} :x: could not read "
                 f"<#{LOGS_CHANNEL}>. See {LOG} on forge.")
        return 1

    # Only a health report counts as the host having spoken up. The Vaultwarden
    # version check also carries host=bastion, and letting it refresh last_seen
    # would mask bastion's own health report having stopped.
    for report in reports:
        if report.get("host") and report.get("_ts") and report.get("kind") == "health":
            state["last_seen"][report["host"]] = report["_ts"]

    all_esc, fresh = escalations(reports, state, now)

    if mode == "triage" and not fresh:
        log(f"triage: {len(reports)} report(s), nothing to escalate")
        if not dry:
            state[key] = newest or cursor
            save_state(state)
        return 0

    roll = rollcall(reports, state, now) if mode == "digest" else None
    summary = ask_agent(reports, fresh if mode == "triage" else all_esc, mode)
    text = compose(reports, fresh if mode == "triage" else all_esc, summary, mode, roll)

    if dry:
        print(f"--- would post to #homelab-status ({mode}) ---\n{text}")
        print(f"\n--- {key} would advance {state.get(key)} -> {newest} ---")
        return 0

    if not post(text):
        # Leave the cursor alone so the next run re-reads rather than loses reports.
        return 1

    for esc_key, _ in (fresh if mode == "triage" else all_esc):
        state["escalated"][esc_key] = now
    state[key] = newest or cursor
    save_state(state)

    log(f"{mode}: {len(reports)} report(s), {len(fresh)} escalation(s) posted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
