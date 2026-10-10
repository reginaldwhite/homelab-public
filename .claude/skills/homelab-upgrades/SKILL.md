---
name: homelab-upgrades
description: Assess which homelab packages need updating and plan or apply the upgrade. Use when asked what needs updating, whether a package should be upgraded, or to plan an upgrade to Caddy, Vaultwarden, AdGuard Home, Plex, OpenClaw, Grafana, the Omada or UniFi WiFi controllers, ollama or open-webui.
---

# Homelab upgrades

Every image tag in this repo is pinned deliberately. `docker compose pull` is the only thing that
moves a tag and nothing runs it on a schedule, so **nothing upgrades on its own** — which is
correct here, and means staleness has to be looked for rather than noticed.

This skill says how to look, how to decide whether it matters, and how to do it safely.

## Two tiers, and why

Sort by **exposure and blast radius, never by how many versions behind something is.** Version
distance ranks risk backwards: an inference server on the LAN can be seventeen minors behind
harmlessly, while the reverse proxy terminating TLS for an internet-facing vault matters one minor
behind.

| Tier | Packages | Handling |
|---|---|---|
| **Watch** | Caddy, Vaultwarden, AdGuard Home, OpenClaw, Grafana, Prometheus, Omada, UniFi | Drifting far is unacceptable — either internet-facing, or holding credentials and able to execute. Check often, upgrade in small hops, and never let one reach six months behind. A security release here is worth interrupting someone for. |
| **Drift** | Plex, ollama, open-webui, and the observability parts that hold no login and face no one: Loki, Alloy, Postgres, blackbox, node exporter, cAdvisor | LAN-only, failure is self-announcing, and nothing else in the homelab depends on them. Report them so they are never invisible, but do not queue them. Upgrade one when you actually want something from it, accepting it will be a project. |

A Drift package sitting a year behind is the intended behaviour, not a backlog. Say so plainly
rather than presenting it as a failure.

**The tier is about stakes, not release speed** — and OpenClaw is the case that makes the
difference matter. Caddy, Vaultwarden and AdGuard together ship under three releases a month, so
staying current on them is nearly free. OpenClaw ships six or seven a month on its own, so being
*current* is not achievable and chasing it is a treadmill. For OpenClaw the goal is therefore
**bounded staleness, not zero**: a scheduled hop on a regular cadence, so it never again reaches
the six months it had drifted to when this was written. Do not read "Watch" as "always current"
for that one; read it as "never allowed to drift far".

## Rules

1. **No version numbers in this skill.** Not in `SKILL.md`, not in a package file. Deployed
   versions come from the running containers; latest comes from upstream. A version written down
   here ages silently — which is exactly how the comment in `forge/docker-compose.yaml` came to
   claim OpenClaw was "about two months behind" when it was six.
2. **Nothing here applies an upgrade on its own.** Bump the tag, commit, then `./deploy.sh <host>`
   by hand. Restarting a container does not update it.
3. **One package at a time.** Never bundle an upgrade with unrelated bumps.
4. **A check that could not run is not an all-clear.** If a host is unreachable or an upstream
   lookup fails, say "unknown", never "current".
5. **Verify after, from the runbook.** An upgrade is not done when the container starts; it is
   done when that package's `Verify after` steps pass.

## Assessing what needs updating

For each package, compare **what is running** against **what upstream publishes**. The per-package
files give the exact container, host and upstream source.

What is running — read the live container, not the repo, so deploy drift shows up too:

```bash
# on the host itself
docker inspect <container> --format '{{.Config.Image}}'
# bastion, from forge or the laptop (robby's key is authorised)
ssh robby@192.168.0.10 "docker inspect caddy --format '{{.Config.Image}}'"
```

What upstream publishes:

```bash
# GitHub-released packages
curl -fsS https://api.github.com/repos/<owner>/<repo>/releases/latest \
  | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1
# Plex, which publishes no release feed
curl -fsS https://plex.tv/api/downloads/5.json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["computer"]["Linux"]["version"])'
```

**From the OpenClaw agent on forge** the exec allowlist permits only the two `curl` lookups
above, exactly as written (no `| sed`, no `| python3 -c`; read `tag_name` or `version` out of
the JSON yourself, and `| head -c 2000` is allowed if the body is long). `docker inspect` and
`ssh` are not available to the agent; the running-version half of the comparison comes from
the laptop, or from the last digest that carried it. Say which half is missing rather than
guessing.

Two traps when comparing:

- **Tag normalisation differs per package.** Each package file declares `tag_from_release` as
  `identity` or `strip-v`. Caddy's release is `v2.11.4` and its docker tag drops the `v`; AdGuard
  keeps it in both. Getting this wrong reports a current package as behind.
- **GitHub's release list is date-ordered and contains non-version tags.** `openclaw/openclaw`
  returns things like `linux-stable` among real releases, so list position means nothing. Use
  `releases/latest`, and when counting how far behind something is, filter to version-shaped tags
  and sort by parsed version.

**cinema is usually asleep**, so Plex's version often cannot be read. That is normal — report the
last known value with its age, and do not wake the machine just to read a version.

## Upgrading a Watch package

These move slowly and the hop is small, so this is ordinary work, not a project.

1. Read `packages/<name>.md` — the hazard, what to back up, and how to verify.
2. Check the backup the runbook names is recent.
3. Bump the tag in the compose file the package file points at. Change the one `image:` line.
4. `./deploy.sh --check <host>` first, then `./deploy.sh <host>`.
5. Work through `Verify after`. If it fails, follow `Rollback` — do not debug forward on a host
   running DNS or the vault.

## Planning a Drift upgrade

A Drift package is usually many releases and months behind, so this needs a written plan before
anything is edited. Produce, in this order:

1. **How far behind, concretely** — releases and elapsed time, from version-shaped tags only.
2. **Intermediate hops.** Do not cross many months in one bite. Pick a mid-point release, land it,
   verify, continue. The plan names the hops.
3. **The contracts to check first.** This is the part people skip. Other things in this repo call
   these packages and parse their output; a major jump can change a CLI flag, an output shape or a
   config schema, and the failure shows up somewhere else entirely. Each package file lists its
   own under `Contracts`.
4. **Backups, verified by age** — not assumed to exist.
5. **Verification and rollback**, from the runbook.

Release notes are the input to this plan. They are large: some of these projects publish more than
100 KB per release, which does not fit in the local model's context on forge. Read them
newest-first, one release at a time, stopping once the breaking changes and any security notes are
in hand — and prefer the project's own upgrade or migration guide over the changelog once the
backlog is more than a few releases deep. When the notes genuinely do not fit, say so and plan
from the migration guide rather than summarising a fraction of them silently.

## Host operating systems

The three Ubuntu hosts are not in the table above, but they age the same way and nothing here
manages them. `unattended-upgrades` installs **security** updates on every host and never
reboots, so two things accumulate: ordinary package updates, and a pending kernel that only a
reboot activates. `apt` on the host is the source of truth, never this file.

Check, on each host:

```bash
apt list --upgradable 2>/dev/null | grep -c upgradable
[[ -e /var/run/reboot-required ]] && cat /var/run/reboot-required.pkgs
```

Apply monthly, or when a reboot is pending, one host at a time, with the script every host
carries (it re-runs itself under `sudo`, logs to `~/logs/os-update.log`, and asks before
rebooting; `--dry-run` shows what it would do):

```bash
/home/robby/homelab-deployed/<host>/scripts/os-update.sh --dry-run
/home/robby/homelab-deployed/<host>/scripts/os-update.sh
```

Order by blast radius: **forge** first (nothing depends on it and it reboots daily anyway), then
**cinema** (Plex only), then **bastion** last and in a quiet window -- its reboot takes DNS and
the vault away for about a minute, and every client pointed at AdGuard has no DNS until it is
back. The script needs a password on every host, so this is done by a person, never by the
agent. After bastion returns: `docker ps` shows its three containers up and
`dig @192.168.0.10 vault.robbywhite.net` answers.

**forge, after an NVIDIA driver update:** ollama takes the GPU through CDI, from a spec file
that describes the installed driver. A new driver makes that file stale and ollama will not
start until it is regenerated, so run this before `docker compose up` or any deploy:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml && nvidia-ctk cdi list
```

`os-update.sh` prints a reminder on forge when the upgrade touched an `nvidia-` package. Why CDI
and not the legacy hook is in the comment above the `devices:` line in `forge/docker-compose.yaml`.

## Per-package detail

One file per package in `packages/`, each carrying a JSON block with the machine-readable facts
and then its runbook:

| Package | File | Tier |
|---|---|---|
| Caddy | `packages/caddy.md` | Watch |
| Vaultwarden | `packages/vaultwarden.md` | Watch |
| AdGuard Home | `packages/adguard.md` | Watch |
| OpenClaw | `packages/openclaw.md` | Watch |
| Grafana | `packages/grafana.md` | Watch |
| Prometheus | `packages/prometheus.md` | Watch |
| Omada controller | `packages/omada.md` | Watch (tag written in full; `latest` is still v5) |
| UniFi Network | `packages/unifi.md` | Watch (moves together with its MongoDB) |
| Plex | `packages/plex.md` | Drift |
| ollama | `packages/ollama.md` | Drift |
| open-webui | `packages/open-webui.md` | Drift |
| Loki | `packages/loki.md` | Drift |
| Alloy | `packages/alloy.md` | Drift (three hosts, one commit) |
| Postgres (events-db) | `packages/postgres.md` | Drift (never cross a major casually) |
| blackbox exporter | `packages/blackbox.md` | Drift |
| node exporter | `packages/node-exporter.md` | Drift (three hosts, one commit) |
| cAdvisor | `packages/cadvisor.md` | Drift (three hosts, one commit) |

Read the package file before touching anything. Every hazard in them was found the hard way and is
recorded in the repo's history.
