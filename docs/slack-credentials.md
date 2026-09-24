# Plan: narrow the Slack credentials the homelab holds

Not implemented. This is the design for replacing the shared bot token on most hosts with
post-only webhooks, and what it is and isn't worth.

## Where the credentials are today

One Slack app (`openclaw`, `A0AJP1R92MD`). Its bot token is `xoxb-…`, currently copied to three
hosts:

| Host | File | Used by | Needs |
|---|---|---|---|
| forge | `~/homelab-deployed/forge/.env` | the `openclaw` container itself | **read + write + socket mode** |
| | | `health-report.py`, `vaultwarden-version-check.sh`, `gpu-guard.sh` | write only |
| bastion | `~/homelab-deployed/bastion/.env` | `health-report.py`, `vault-report.py` | write only |
| | | `digest-watchdog.py` | **read** (`conversations.history`) |
| cinema | `~/homelab-deployed/cinema/.env` | `health-report.py` | write only |

The token's granted scopes are broader than any script needs:

```
users:read, app_mentions:read, channels:history, channels:read, chat:write,
groups:history, im:history, reactions:read, im:write, chat:write.public
```

`channels:history` is the one that matters. Anything holding this token can **read every message
in every channel the app is in** — and one of the hosts holding it, bastion, is the machine
running the password vault.

## What is actually wrong with that

Not much, honestly, and it is worth saying so plainly before proposing work:

- The token never leaves the LAN, and all three hosts are already trusted with more than Slack.
- bastion holding it is the only placement that gives real pause, and bastion is also the host
  best protected.
- The blast radius of a leak is "someone can read and post in a homelab Slack", not the vault.

What makes it worth doing anyway is the **direction of travel**: every new reporter added so far
has meant another copy of a read-capable token on another host. cinema got one for the sole
purpose of posting a health line four times a day. That is the wrong default, and the cost of
changing it is small.

## Target state

The bot token exists on **forge only**, where the OpenClaw gateway genuinely needs it — socket
mode, reading mentions, posting replies. Everything else posts through a webhook that can do
nothing but append to one channel.

| Host | Holds | Can do |
|---|---|---|
| forge | bot token (gateway) + both webhooks (scripts) | everything — unavoidable, it *is* the Slack app |
| bastion | both webhooks | post to `#homelab-logs` and `#homelab-status`, nothing else |
| cinema | logs webhook | post to `#homelab-logs`, nothing else |
| vault | — | nothing; bastion reports on its behalf |

An incoming webhook URL is still a secret — anyone holding it can post to that channel as the
app — but it cannot read anything, cannot post elsewhere, and is revocable on its own without
disturbing the other hosts or the gateway.

## The one thing blocking it

`digest-watchdog.py` on bastion has to know whether a digest appeared in `#homelab-status`, and
it currently answers that by calling `conversations.history`. **A webhook cannot read.** So as
long as the watchdog works that way, bastion keeps a read-capable token and the whole exercise
buys nothing there.

The fix is to stop asking Slack. Have the digester, after a digest **posts successfully**, touch
a file on bastion over the SSH path it already has:

```bash
ssh robby@192.168.0.10 touch /home/robby/logs/.digest-heartbeat
```

and have the watchdog check that file's mtime instead of reading the channel.

Trade-offs, both ways:

- **Better:** it tests "the digester completed a run end to end", which is the thing being
  watched, and it keeps working if the Slack API is degraded.
- **Worse:** it adds a forge → bastion SSH dependency to the digest path, and if that SSH breaks
  while Slack is fine the watchdog raises a false alarm. That is the safe direction to fail —
  "the digester might not be running" when it is, rather than silence when it isn't — but it is
  a new way to be woken up for nothing.
- **Neutral:** the heartbeat and the digest can disagree if the post succeeds and the SSH fails.
  Log both.

This step is optional. Doing only cinema and forge's scripts is most of the benefit for none of
the risk.

## Steps

| # | Work | Verify |
|---|---|---|
| 1 | Create two incoming webhooks on the `openclaw` app — one for `#homelab-logs`, one for `#homelab-status` | `curl -X POST -d '{"text":"webhook test"}' <url>` lands in the right channel |
| 2 | Add `SLACK_LOGS_WEBHOOK_URL` to cinema's `.env`; delete `SLACK_BOT_TOKEN` from it | `health-report.py --test` still posts; the log line reads `posted via webhook` |
| 3 | Add both webhook URLs to forge's `.env`, leaving the bot token for the container | `gpu-guard.sh --dry-run`, `vaultwarden-version-check.sh --test` |
| 4 | Add both to bastion's `.env` | `health-report.py --test`, `vault-report.py --dry-run` |
| 5 | *(optional)* Switch the watchdog to the heartbeat file, then delete bastion's `SLACK_BOT_TOKEN` | stop the digester for 27h and confirm the watchdog still tags |
| 6 | *(optional)* Narrow the app's scopes to what the gateway needs, dropping `channels:read`, `groups:history`, `reactions:read`, `users:read` if nothing uses them | the gateway reconnects and still answers a Slack mention |

Every script already prefers a webhook when one is present and falls back to the token, so steps
2–4 are `.env` edits with no code change and no deploy. `.env` files are never pushed by
`deploy.sh`, so they survive.

## Rollback

Put `SLACK_BOT_TOKEN` back in the host's `.env` and remove the webhook line; the scripts fall
back automatically. If a webhook leaks, delete it in the Slack app config — the other hosts and
the gateway are unaffected, which is the property the whole plan is buying.

## Deliberately not doing

- **A second Slack app for the reporters.** Considered while building the digester and rejected:
  reports go to `#homelab-logs` and digests to `#homelab-status`, so the digester reads one
  channel and writes the other and can never consume its own output. A separate app identity was
  belt-and-braces for a problem the channel split already solved.
- **Per-host webhooks.** One webhook per *channel* is enough. Per-host would allow revoking one
  machine without the others, but Slack's webhook list is not labelled usefully enough to keep
  four of them straight, and the hosts are not independently compromisable in any realistic way.
- **A secret manager.** The vault on bastion could store these, but every consumer would then
  need a vault credential to fetch them, which moves the problem rather than solving it.
