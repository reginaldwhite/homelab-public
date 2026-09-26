# Keeping the homelab current: two tiers, one script on forge

> **Status (2026-09-26): phase 1 partly built.** The skill exists at
> [.claude/skills/homelab-upgrades/](../.claude/skills/homelab-upgrades/) — `SKILL.md` and seven
> package runbooks. Still to do in phase 1: the `deploy.sh` addition that pushes `.claude/skills/`
> to a host, and the `apply.sh` entries that install it into forge's state dir and validate it.
> Phases 2–4 are unbuilt.
>
> This supersedes four earlier drafts of this document, all of which sorted packages by how far
> behind they were. That was the wrong measure — see below.

Every image tag here is pinned deliberately. Pinning is right: `docker compose pull` is the only
thing that moves a tag, nothing runs it on a schedule, and Vaultwarden's migrations are one-way.
But it turns a silent-upgrade problem into a silent-staleness problem, and today only Vaultwarden
has anything watching for it.

This generalises [forge/scripts/vaultwarden-version-check.sh](../forge/scripts/vaultwarden-version-check.sh)
from one package to seven. It stays **entirely inside the homelab**: no repo clone on a host, no
GitHub credential anywhere new, no dependency on the laptop being awake.

## Version distance is not risk

Measured 2026-09-26 from the GitHub release feeds:

| Package | releases/mo | last 90d | median notes | p90 notes | pinned → latest |
|---|---|---|---|---|---|
| Caddy | 0.6 | **0** | 1.9 KB | 18 KB | `2.10.2` → `v2.11.4` |
| Vaultwarden | 0.9 | 4 | 1.0 KB | 4.3 KB | `1.37.3` → current |
| AdGuard Home | 1.3 | 2 | 1.3 KB | 3.1 KB | `v0.107.72` → `v0.107.79` |
| open-webui | 4.6 | 8 | 2.8 KB | 32 KB | `0.8.8` → `v0.11.4` |
| OpenClaw | 6.5 | 14 | **40 KB** | **115 KB** | `2026.3.2` → `v2026.9.6` |
| ollama | 8.8 | 27 | 0.7 KB | 1.4 KB | `0.17.5` → `v0.34.4` |
| Plex | n/a | n/a | n/a | n/a | `1.43.2.10687` → `1.43.4.10903` |

Plex publishes no release feed; `plex.tv/api/downloads/5.json` → `computer.Linux.version` gives a
version and nothing else, in the same format the `plexinc/pms-docker` tag uses.

Three things fall out, and together they define the strategy.

**A monthly queue cannot converge.** ollama, OpenClaw and open-webui ship about **20 releases a
month between them**. Any process that upgrades one package a month does not fall behind slowly
— it falls behind by construction, permanently. Caddy, Vaultwarden and AdGuard total **2.8 a
month**, which a monthly cadence keeps current effortlessly.

**Sorting by version distance ranks risk backwards.** ollama is seventeen minors behind and it
does not matter: it is an inference server on the LAN. Caddy is one minor behind and it matters
more than all three fast movers combined, because it terminates TLS for an internet-facing vault.
Earlier drafts of this document classified by version arithmetic and would have had you spend
three months upgrading ollama, OpenClaw and open-webui while Caddy sat in the "routine" pile.

**The packages that need judgement are the ones whose notes fit locally.** forge's whole prompt
budget is 16384 tokens (`OLLAMA_CONTEXT_LENGTH`, and OpenClaw's own floor is 16000). AdGuard's
notes are ~318 tokens at the median and ~787 at p90; Caddy ~489 and ~4,545; Vaultwarden ~246 and
~1,079. All fit with large margins. OpenClaw's are ~9,900 at the median and ~28,700 at p90 — and
that one does not fit. The alignment is lucky and it is what makes self-containment work.

## Two tiers, by exposure and blast radius

| Tier | Packages | Combined cadence | Handling |
|---|---|---|---|
| **Watch** | Caddy, Vaultwarden, AdGuard Home, OpenClaw | 9.3/mo | Checked daily, upgraded in small hops, never allowed to reach six months behind. The only tier where a security release interrupts you. |
| **Drift** | Plex, ollama, open-webui | ~13/mo | Reported so it is never invisible, but never proposed and never queued. Upgraded opportunistically, accepting that it will be a project. |

Watch is where drifting far is unacceptable — internet-facing, house-critical, or credential-holding
and able to execute. Caddy terminates TLS for the vault, AdGuard is DNS and DHCP for the house,
Vaultwarden holds every password, and OpenClaw holds a key to three hosts behind a LAN-bound gateway
with unprompted execution. Drift is LAN-only and fails visibly — you notice a broken Plex or web UI
the moment you use it.

**OpenClaw moved from Drift to Watch on review, and it is the awkward case.** Sorting it by exposure
put it in Drift; sorting by blast radius puts it in Watch, and blast radius is the right criterion
for a package that sits at the centre of the homelab. But it also ships 6.5 releases a month against
2.8 for the other three Watch packages combined, so the goal for it is **bounded staleness rather
than currency** — a scheduled hop keeping the gap in months, not an attempt to track releases.

**This replaces the monthly queue rather than scheduling it.** Watch items are acted on when
flagged, because 2.8 a month is not a queue. The monthly ritual that remains is a **Drift
review**: once a month, look at the Drift rows and decide whether you want one. Usually the
answer is no, and that is a legitimate answer rather than a backlog.

**Nothing in the Drift tier auto-updates.** Considered and rejected for the two where it looked
cheapest: Plex has a library database people notice, and OpenClaw's config is deploy-managed
(`apply.sh` copies `openclaw.json` into the state dir, and OpenClaw rewrites it at runtime — see
`meta.lastTouchedVersion`), so a version jump arriving without a deploy can fight it.

## What runs where

Everything is on forge, and every piece already has a working precedent:

| Need | How | Precedent |
|---|---|---|
| what is actually running | `docker inspect` — forge reads its own containers, SSHes to bastion as `robby` | `vaultwarden-version-check.sh` does exactly this today |
| upstream latest | anonymous HTTPS to GitHub and plex.tv | same script, and verified for all seven |
| tier and staleness | deterministic | — |
| a Watch-tier summary | `gpt-oss:20b`, notes fit in 16384 | the digester already calls the model for prose |
| delivery | a report to `#homelab-logs`; the digester decides who is interrupted | every other producer in this repo |

**Live `docker inspect` rather than the repo's pinned tags** — a deliberate change from earlier
drafts. It reports what is running rather than what is intended, which is strictly more useful
and makes pin drift visible instead of out of scope. It is also what the existing script already
does, for the reason its comment gives: *"what bastion is actually running (not what the repo
intends)"*.

**cinema is the exception.** forge→bastion SSH as `robby` is proven; **forge→cinema is not** —
`plex-power.py` reaches cinema with the *agent* key under a forced command permitting only
`suspend` and `health`. Cinema is also asleep most of the time. So Plex's version is read when
cinema happens to be awake and otherwise reported as a last-known value with its age. Plex is in
the Drift tier, so a stale reading costs nothing.

**No GitHub credential, and no pull requests from forge.** Watch is three packages at 2.8
releases a month; bumping a tag by hand is a one-line edit and a `deploy.sh`. That satisfies
"never more than one PR open" by construction, because nothing opens one automatically. If a PR
is wanted for a Drift-tier upgrade, it is opened from the laptop with the PAT already in
`.secrets.env`.

## The script

`forge/scripts/version-check.py`, run daily from `forge/crontab`, absorbing and replacing
`vaultwarden-version-check.sh`. Per run:

1. Read the running image per tracked container — locally, and over SSH to bastion.
2. Resolve upstream latest per package.
3. Compare, and classify each stale package by its declared tier.
4. For a stale **Watch** package only, summarise its release notes with the local model and match
   them against `CVE-\d`, `GHSA-`, `security fix`, `vulnerability`.
5. Post one `kind: version` report to `#homelab-logs`.

It inherits the behaviours the existing script earned: wait for the network before believing a
lookup failed, treat a check that could not look as `error` rather than an all-clear, `--test`
because a job silent for weeks hides a broken notifier, and `--dry-run`.

The model is called **only** for a stale Watch package — at most three, rarely more than one, and
never for the Drift tier whose notes do not fit. A run with nothing stale in Watch calls no model
at all. The summary is escaped before posting, as `clean_summary` already does in the digester:
release notes are text fetched from the internet, so "summarise this" is untrusted input.

## Escalation

The digester keeps deciding who gets interrupted; this only feeds it.

| Condition | Result |
|---|---|
| a Watch package is behind **and** the CVE pattern matches | escalate on the next triage run |
| a Watch package is behind | daily digest block |
| a Drift package is behind | daily digest block, never escalated |
| Vaultwarden behind | escalate — preserves today's behaviour and its reason: clients refuse to sync against a server that is too old and fail with an opaque decoding error, so you otherwise find out from a phone |
| the check could not run | `status: error`, which the digester already escalates |

One change to `health-digest.py`: generalise the `kind == "vaultwarden-version"` rule to
`kind == "version"` with the tier and CVE match carried in the report. The old script is deleted
**after** that rule lands, never before, or the only version alert that exists today disappears
for a phase.

`EXPECTED_SILENCE_S` is keyed by host, not kind, so a dead version checker looks exactly like one
with nothing to say. This adds a kind-aware window (`{"version": 3 days}`) — without it this is
the shared-cursor bug again: a component that looks healthy while silently doing nothing.

## The skill

`.claude/skills/homelab-upgrades/` — `SKILL.md` plus one file per package holding the tier, the
upstream source, and the runbook: hazard, what to back up first, how to verify, how to roll back.

**This is the valuable half, and it is worth building even if the script never is.** The compose
comments already said *"about two months behind — bump deliberately."* The information was never
missing. What is missing is the upgrade being cheap and reversible enough to just do, and that is
what a written runbook per package fixes.

Two readers, one file: deployed into `/home/robby/openclaw-state/skills/` by `apply.sh`'s existing
`CONFIG` array, so asking *"@openclaw should I upgrade caddy?"* in Slack is answered from the same
runbook; and loaded from the working tree by Claude Code on the laptop when a Drift-tier upgrade
needs a real plan.

Constraints on the format, verified against the **deployed** OpenClaw (`2026.3.2`), not `main`:
skills are [AgentSkills](https://agentskills.io)-compatible, so `SKILL.md` serves both runtimes —
but OpenClaw's parser takes **single-line frontmatter keys only** and `metadata` must be
single-line JSON. A wrapped description loads on the laptop and silently fails on forge, so the
file is written to the stricter parser and phase 1 checks both. The path `~/.openclaw/skills` is
stable across 2026.3.2 and 2026.9.6, and inside the container it is the state-dir bind mount.

`deploy.sh` needs one addition: it rsyncs `<host>/` and merges `shared/` into that host's
`scripts/`, so a repo-root directory reaches no host today. It must push `.claude/skills/`
specifically — not `.claude/`, which also holds local-only settings.

## What this deliberately does not do

Recorded so they are not rediscovered. Every one of these was designed in an earlier draft.

- **No laptop dependency, no launchd, no scheduled work outside a host crontab.** The earlier
  pull-based design put the check on the laptop because it read the repo's pins; reading live
  containers removes the reason.
- **No GitHub credential on forge, no PR automation, no branch protection, no working clone.**
- **No release-note analysis for the Drift tier.** OpenClaw's p90 release body alone is ~28,700
  tokens against a 16384 ceiling. A local summary would be written from a fraction of the notes
  without saying so; the laptop is the escalation for exactly this case.
- **No `kind: images` reports and no `images` verb.** `docker inspect` over SSH is already proven
  and needs no change to the byte-identical shared reporter.
- **No version-distance classification.** Tier is declared per package from exposure and blast
  radius. A rule derived from version numbers ranked Caddy below ollama.
- **No monthly queue.** Watch is acted on promptly; Drift is never queued.

## Failure modes

| Failure | Behaviour |
|---|---|
| an upstream source is unreachable | retry, then `status: error` for that package only; the others still report |
| bastion unreachable over SSH | `status: error` — and the digester escalates it, which is right: two of three Watch packages live there |
| cinema asleep | Plex reported as last-known with its age, never `error`; cinema never escalates anywhere in this repo |
| a package file will not parse | `apply.sh --validate` fails the deploy before it reaches the live copy, as it already does for `openclaw.json` |
| the model is unavailable or returns junk | the version comparison and CVE match are unaffected; the summary is omitted |
| a Drift package drifts for a year | visible in every digest, escalated never. That is the intended behaviour, not a gap |
| the release feed is date-ordered with non-version tags | version-shaped tags only, sorted by parsed version — `openclaw/openclaw` returns `linux-stable` among real tags and list position is meaningless |
| the check stops running | the kind-aware silence window above; without it, nothing notices |

## Phasing

| Phase | Work | Done when |
|---|---|---|
| 1 | `.claude/skills/homelab-upgrades/` — `SKILL.md` and seven package files; `deploy.sh` pushes it; `apply.sh` deploys and validates it | the skill loads on **both** runtimes and every package file parses |
| 2 | `version-check.py`: live `docker inspect`, upstream latest, tiers, `kind: version` report — **no model yet** | one report reproducing the drift table above |
| 3 | digester: `kind: version` rule, CVE escalation for Watch, kind-aware silence window; **then** delete `vaultwarden-version-check.sh` | a Drift bump reaches only the digest; a Watch CVE tags on triage |
| 4 | the local summary for stale Watch packages | a Caddy bump arrives with a readable summary, and nothing is called for Drift |

Phase 1 stands alone and is the part most likely to change behaviour. Phase 4 is optional: the
tiering and the CVE match already put the right three packages in front of you.
