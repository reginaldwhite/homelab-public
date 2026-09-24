# Health reporting: machines report, the agent digests

## What this changes

Today every producer decides, by itself, whether to interrupt Robby:

```
bastion  health-report.sh ──┐
forge    vw-version-check ──┼──► #homelab-status ──► @robbywhite
forge    server-log-monitor ┤     (each one tags him directly)
forge    cinema-log-monitor ┘
```

Each producer is its own severity judge, nothing correlates across hosts or across days, and the
channel is where raw output lands. Robby is the correlator.

The target:

```
bastion ─┐
forge   ─┼─► #homelab-raw ──► digester (on forge) ──► #homelab-status ──► @robbywhite
cinema  ─┤    structured        reads, rules,          digest + only        only when
vault   ─┘    reports           correlates             real escalations     action is needed
                                      ▲
                                      └── bastion watchdog: tags Robby if the digest goes silent
```

Machines stop deciding what is urgent. They report facts. One digester decides what deserves a
human, and Robby gets a daily summary plus interruptions only when something needs him.

## Why the current shape doesn't scale

It works, but it has three structural problems, and they get worse as hosts are added:

1. **Severity is decided in the wrong place.** `health-report.sh` tags Robby whenever any section
   isn't `none`. A single transient journal error and a disk at 97% produce the same interruption.
   A shell script can't tell them apart without becoming a rules engine.
2. **Nothing correlates.** "Disk at 82%" is noise on Monday and an emergency by Friday if it has
   been climbing 3% a day. Nothing today can see that, because each report is evaluated alone.
3. **Volume scales with hosts × checks.** Four hosts reporting twice daily is eight messages, and
   the all-clears train Robby to stop reading the channel — which is precisely how the backup
   failing every week for four months went unnoticed.

## Constraints discovered by testing, not assumed

These four facts shaped the design. Three of them rule out the obvious implementation.

**1. Reports posted with OpenClaw's token are indistinguishable from the bot's own messages.**
bastion currently posts using `SLACK_BOT_TOKEN`, so its reports arrive as `app_id A0AJP1R92MD`,
`user U0AJY4AETS7` — the same identity OpenClaw itself posts under. Verified with
`message read --json`. A digester filtering "messages that are reports" could only guess from the
text, and could not reliably avoid digesting its own output.

→ **Reporters need their own Slack app,** so `app_id` is a deterministic filter.

**2. Mentioning the bot cannot be the trigger.** Two independent reasons:

- An app does not receive `app_mention` for its own messages, so while bastion posts with
  OpenClaw's token, the mention is inert.
- More fundamentally, **Slack Socket Mode has no replay.** forge is powered off 11pm–5am, so its
  socket is closed. Every event posted in that window is delivered to nobody and never retried.
  An event-driven digester would silently lose every overnight report — the same class of silent
  gap this whole effort exists to remove.

→ **The digester polls with a cursor.** The `<@U0AJY4AETS7>` mention stays in reports as a human
signal of ownership, but nothing depends on it.

**3. Polling needs no Slack plumbing.** `openclaw message read --channel slack --target
channel:C… --after <ts> --limit N --json` already exists and works against `#homelab-status`
(verified). It returns `ts`, `app_id`, `bot_id` and `text` per message, which is everything a
cursor-based reader needs.

**4. The LLM can be called without giving it control of delivery.** `openclaw agent --message …
--json` runs one turn and returns the reply to the caller instead of posting it. So the
deterministic parts — what counts as serious, what gets posted, what the cursor is — stay in a
script, and the model is asked only for prose and correlation.

## The availability inversion, and why the watchdog is not optional

This is the one real risk in the whole idea, and it needs stating plainly.

Today bastion's alert path is bastion → Slack → Robby. bastion is the always-on host. Putting the
agent in the middle makes that path depend on **forge**, which is the *least* available machine
here: down 11pm–5am by design, and down for two months in mid-2026 after a power event. We
removed exactly this dependency two changes ago, for exactly this reason.

So the plan reintroduces it deliberately — but only for *judgement*, never for *liveness*:

**bastion runs a watchdog.** Once a day it checks `#homelab-status` for a digest newer than 26
hours. If there isn't one, it tags Robby directly:

> `@robbywhite ⚠️ no health digest since <time> — the digester on forge is not running. Raw
> reports are still in #homelab-raw.`

This is the only machine→Robby tag that survives, and it is the one that cannot be delegated:
a digester that has stopped cannot report that it has stopped. It costs one cron entry on a host
that already has the credential and the schedule.

Without it, a forge outage silently ends *all* alerting — the July–September failure, moved one
layer up and made harder to see.

## Components

### 1. A reporter Slack app (`homelab-reporter`)

A second Slack app, incoming webhooks only. Per-host webhook URLs so one can be revoked without
touching the others.

Two benefits beyond the filtering in constraint 1: a webhook is **post-only to one channel**, so
bastion — the host holding the password vault — no longer needs a token that can read channel
history; and revocation is per host.

### 2. Report format

Reports must be parsed by a script, skimmed by a human, and summarised by a 20B model. One
summary line, then a fenced JSON block:

```
<@U0AJY4AETS7> bastion health: ok (0 findings)
```json
{"host":"bastion","kind":"health","status":"ok","ts":"2026-09-24T08:00:03-05:00",
 "findings":[],
 "metrics":{"fs_max_pct":17,"backup_age_days":1}}
```
```

- `status`: `ok` | `warn` | `error` — the producer's *factual* classification, not a severity
  judgement. `error` means "a check could not run", not "this is urgent".
- `findings`: one object per non-clean section, `{"check":…, "detail":…}`.
- `metrics`: numbers worth trending. This is what makes correlation possible at all — a digester
  can only spot "climbing 3% a day" if the number is in the report rather than buried in prose.

Producers keep logging locally exactly as they do now; Slack is a transport, not the record.

### 3. The digester (deployed script on forge)

`forge/scripts/health-digest.sh`, installed by `deploy.sh` and run from `forge/crontab` — not an
OpenClaw cron job. Deliberate: the deployment model says runtime config is version-controlled and
deployed, and OpenClaw's cron lives in `~/openclaw-state/cron/jobs.json`, which is agent state
that no deploy can reproduce. The existing `cinema-log-monitor` stays in agent cron because it is
a genuine agent task; this is a script that *calls* an agent.

Per run:

1. Read `#homelab-raw` since the stored cursor (`~/openclaw-state/digest-cursor.json`), clamped to
   7 days so a lost cursor can't replay months.
2. Drop anything whose `app_id` is not the reporter app. Parse the JSON blocks; a report that
   won't parse is itself a finding.
3. Apply the deterministic rules below.
4. If there is anything to say, call `openclaw agent --json` with the parsed reports plus the last
   7 days of digests, and ask for a summary, a correlation, and one recommended action.
5. Post to `#homelab-status`. Tag Robby only if step 3 says to.
6. Advance the cursor **only after a successful post**, so a Slack failure retries rather than
   loses reports.

Two schedules:

| Run | When | Posts |
|---|---|---|
| triage | hourly, 6am–10pm | only if something escalates |
| digest | 07:30 daily | always — so silence is itself the signal the watchdog looks for |

### 4. Escalation policy

Determinism where it matters; judgement only where it adds something. The model never decides
whether the serious categories are serious.

**Escalate immediately (tag Robby, on the next triage run):**

- any `status: error` — a check that could not run is never an all-clear
- backup to vault failed, or last success older than 8 days
- any filesystem ≥ 90%
- a host that has missed its expected report window (below)
- Vaultwarden behind the latest release

**Daily digest only:**

- filesystems 80–89%
- isolated journal errors, stopped containers that are expected to exit
- image drift on anything but Vaultwarden

**Never:** all-clear reports.

**Left to the model:** the prose summary, cross-day correlation, trend extrapolation, and the
recommended action. Every escalation carries the raw finding text underneath it, so a bad summary
is visible rather than authoritative.

**Expected report windows** (absence detection):

| Host | Cadence | Alert if missing for |
|---|---|---|
| bastion | daily 08:00 | 26h |
| forge | daily at boot (~05:00) | 26h |
| vault | Sunday window | 8 days |
| cinema | only when awake | never — absence is normal; forge's `cinema-log-monitor` stays as its external check |

## Phasing

Each phase is independently useful and independently revertible.

| Phase | Work | Done when |
|---|---|---|
| 0 | Create `homelab-reporter` app + `#homelab-raw`; invite both apps; per-host webhooks into each host's `.env` | a `curl` from each host lands in raw |
| 1 | `health-report.sh` → structured format, posts to raw via the reporter webhook, tags the bot, stops tagging Robby | bastion's daily report is machine-parsable |
| 2 | Digester script + `forge/crontab` entries; `--dry-run` that prints what it *would* post | a day of real reports produces one sensible digest |
| 3 | bastion watchdog | stopping the digester for 27h produces a tag |
| 4 | Remaining producers: forge self-report at boot, vault reports during its window, `vaultwarden-version-check.sh` → a report instead of a DM | all four hosts visible in raw |
| 5 | Retire `server-log-monitor` — absence detection replaces the external bastion check | one fewer agent cron job |

Phase 3 lands before phase 4 on purpose: the watchdog must exist before anything relies on the
digester.

## Failure modes

| Failure | Caught by |
|---|---|
| forge down, digester silent | bastion watchdog (the only remaining machine→Robby tag) |
| bastion down | digester's absence detection; if both are down, the watchdog is also down — see the open decision below |
| digester under-escalates (LLM misjudges) | deterministic rules cover the serious categories; raw reports remain in `#homelab-raw` |
| digester over-escalates | tuning the rules; noise here is recoverable, silence is not |
| cursor lost or corrupt | 7-day clamp, then re-digest; duplicates are harmless |
| Slack outage | cursor only advances after a successful post, so reports are re-read, not lost |
| reporter webhook leaked | post-only to one channel; revoke that host's webhook alone |
| bot digesting its own output | reporter app has a different `app_id`; filter is exact, not textual |

## Open decisions

1. **Separate `#homelab-raw`, or keep everything in `#homelab-status`?** Recommend separate: the
   channel Robby watches should contain only things meant for him. Costs one channel.
2. **Keep a deterministic bypass for catastrophes?** e.g. filesystem ≥ 95% or vault backup failed
   → bastion tags Robby directly, without waiting for forge to wake. This contradicts "machines
   tag the bot, not me", but it is the only way to hear about a full disk before morning.
   Recommend keeping it for two or three conditions only.
3. **Is a 20B local model good enough for the judgement half?** The deterministic rules mean a bad
   digest degrades to "noisy or bland", not "missed outage". Worth reviewing after a week of real
   digests against the raw channel.
4. **Hourly triage 6am–10pm, or only the daily digest?** Hourly costs ~5s of GPU per run and
   buys same-day escalation; daily-only is simpler and everything waits for 07:30.
