# Homelab

## Network

| Device | IP |
|---|---|
| ATT Fiber Router | `192.168.0.1` |
| `bastion` (file) | `192.168.0.10` |
| `cinema` (plex-7600t) | `192.168.0.13` |
| `forge` (7700k) | `192.168.0.12` |
| `vault` (tower) | `192.168.0.212` |

Subnet: `192.168.0.0/24`

---

## Servers

### `bastion` — 192.168.0.10 (Ubuntu) — Always On

General services and scheduling hub. Docker compose: [bastion/vaultwarden/docker-compose.yml](bastion/vaultwarden/docker-compose.yml) (Compose project `vaultwarden`)

| Container | Image | Port(s) | Notes |
|---|---|---|---|
| `vaultwarden` | `vaultwarden/server:1.37.3` | 8080→80 | Bitwarden-compatible vault at `vault.robbywhite.net`, signups disabled. Version pinned — see below |
| `caddy` | `caddy:latest` | 80, 443 | Reverse proxy / TLS termination |
| `adguardhome` | `adguard/adguardhome` | host network (53, UI on 8888) | DNS-level ad & tracker blocking — but see below, most clients bypass it today |

> **Most clients never reach AdGuard.** The BGW320-500 advertises *itself* as the DNS server
> over DHCP and resolves publicly rather than forwarding to AdGuard, so only hosts with DNS set
> statically to `192.168.0.10` (forge) are filtered.
>
> This also breaks Bitwarden sync on WiFi. Clients resolve `vault.robbywhite.net` to the public
> IP, and the gateway's NAT loopback stalls on transfers beyond a few KB — so a first sync never
> completes, while later delta syncs squeak through. Quick fix for any device: set its DNS
> manually to `192.168.0.10`; the AdGuard rewrite for that name already exists.
>
> [bastion/adguard-dhcp-runbook.md](bastion/adguard-dhcp-runbook.md) is the plan to fix it for
> every client by making AdGuard the DHCP server. **Not yet executed** — it has real costs,
> chiefly that bastion becomes required to join the network.

#### Deploying changes

`./deploy.sh bastion` (see [Deploying](#deploying)) pushes `bastion/` to
`/home/robby/homelab-deployed/bastion/` and runs [bastion/apply.sh](bastion/apply.sh) there:

| Source | Applied as |
|---|---|
| `bastion/vaultwarden/` | `docker compose up -d` from the deployed copy, then `caddy reload` (validated first) |
| `bastion/crontab` | installed as robby's crontab |
| `bastion/scripts/*.sh` | run by cron straight from the deployed copy |

If the Vaultwarden tag changed, `apply.sh` backs up `vw-data` before upgrading.

Live data is **not** in the repo or the deployed copy: `vw-data`, `agh-work` and `agh-conf`
stay in `/home/robby/vaultwarden/`, referenced by absolute path, which is also what
`backup-to-tower.sh` pushes to `vault`. Caddy's certificates live in the
`vaultwarden_caddy_data` volume; the pinned project name keeps it attached.

Don't run `docker compose` from `~/vaultwarden/` or from a git clone. Both would reapply
whatever config happens to be on disk there.

#### Reverse proxy ([Caddyfile](bastion/vaultwarden/caddy/Caddyfile))

| Hostname | Upstream | TLS |
|---|---|---|
| `vault.robbywhite.net` | `vaultwarden:80` (compose network) | Let's Encrypt |
| `dns.robbywhite.net` | `192.168.0.10:8888` (AdGuard admin) | `tls internal` |

Two things to know when editing it:

- **`127.0.0.1` inside the Caddy container is the container**, not the host. Anything running
  with `network_mode: host` (AdGuard) or reached via a published port must be addressed by the
  host's LAN IP. Services on the same compose network are reachable by container name.
- **LAN-only hostnames need `tls internal`.** `dns.robbywhite.net` has no public DNS record, so
  public ACME validation can never succeed; Caddy retried indefinitely and was pushed onto the
  Let's Encrypt staging CA, which risks the renewal of `vault.robbywhite.net` alongside it.

The deploy validates Caddyfile changes in a throwaway container before they reach the live
copy, then runs `caddy reload`, never a restart, so the vault never drops. The `caddy/`
directory is bind-mounted rather than the file itself. A single-file mount pins the inode,
and rsync and git both replace files rather than editing them, so the container would keep
serving the previous Caddyfile and a reload would silently reapply stale config.

#### Vaultwarden version is pinned

Bitwarden client compatibility is version-sensitive: clients refuse to sync against a server
that is too old, and the failure surfaces as an opaque decoding error rather than a version
warning. Vaultwarden **1.37.2+** is required for clients **2026.8.0** and newer.

`:latest` is misleading here — it only moves when something runs `docker compose pull`, which
nothing does on a schedule, so the server sat on 1.35.3 for seven months while the tag said
"latest". The tag is pinned so the deployed version is visible in git.

To upgrade: bump the tag in [bastion/vaultwarden/docker-compose.yml](bastion/vaultwarden/docker-compose.yml),
merge to `main`, and run `./deploy.sh bastion`. Vaultwarden's DB
migrations are not reversible, so when the tag changes the script stops the container and
tars `vw-data` into `/home/robby/vw-backups/` before starting the new version. Confirm with:

```bash
docker logs vaultwarden | grep Version
```

#### Staleness alert

Pinning means nothing upgrades on its own, so
[forge/scripts/vaultwarden-version-check.sh](forge/scripts/vaultwarden-version-check.sh)
compares the tag bastion is actually running against the newest GitHub release and DMs
you in Slack through OpenClaw when it's behind. It reports the *deployed* tag
(`docker inspect`), not the one the repo intends, so drift between the two shows up too.

It runs on **forge**, from the crontab in [forge/crontab](forge/crontab), installed by
`./deploy.sh forge`. Cron runs the *deployed* copy, never the git tree — pointing it into a
working tree means a branch switch can make the script vanish and silently break the job.

```cron
@reboot /home/robby/homelab-deployed/forge/scripts/vaultwarden-version-check.sh
```

Forge is the right host even though the service lives on bastion: OpenClaw sends the
Slack message and only runs on forge, so firing the check from bastion could alert at a
time when nothing can deliver it. Since bastion wakes forge at 5am daily, "at boot" gives
a once-a-day check for free, always at a moment the sender is up. The script waits up to
ten minutes for the OpenClaw container before giving up.

A check that *fails* also alerts — an unreachable bastion or GitHub is reported rather
than silently skipped, since a silently broken notifier is the exact failure being guarded
against. To re-verify delivery at any time:

```bash
forge/scripts/vaultwarden-version-check.sh --test    # sends a sample Slack DM
```

The script never upgrades anything by itself. Restarting a container does **not** update
it — only `docker compose pull` fetches a new image — and Vaultwarden's migrations are
one-way, so upgrades stay manual and backed up.

**Cron schedule** (controls all other servers), from [bastion/crontab](bastion/crontab):

| Time | Job |
|---|---|
| 5:00am daily (Mon–Sat) | Wake `forge` via WoL |
| 5:00am Sunday | Wake `forge` via WoL (after backup window) |
| 11:00pm daily | Shut down `forge` via SSH |
| 2:30am Sunday | Push bastion data → `vault` (`backup-to-tower.sh`) |
| 2:45am Sunday | Wake `forge` for backup |
| 3:00am Sunday | Sync forge data → `vault` (`sync-forge.sh`) |
| 3:45am Sunday | Sleep `forge` after backup |

Scripts: [bastion/scripts/](bastion/scripts/). Both the crontab and the scripts are
installed by `./deploy.sh bastion`, so edit them here rather than with `crontab -e` on bastion.

---

### `cinema` — 192.168.0.13 (Ubuntu) — On Demand

Dedicated Plex Media Server. Woken via WoL by OpenClaw agent on `forge` when needed.
Docker compose: [cinema/docker-compose.yml](cinema/docker-compose.yml) (Compose project `plex`)

- **MAC:** `d0:17:c2:dd:c1:c2`

| Container | Image | Port(s) | Notes |
|---|---|---|---|
| `plex` | `plexinc/pms-docker:latest` | host network (32400) | Config in `/root/plex/config`, media in `/mnt/storage/plex` → `/data` |

The stack was first started with `sudo`, so the config/transcode bind mounts live under
`/root/plex/` and the compose file uses absolute paths to keep pointing at them.
`PLEX_CLAIM` is only needed when claiming a fresh server — put it in `cinema/.env`.

---

### `forge` — 192.168.0.12 (Ubuntu) — 5am–11pm Daily

AI and automation server with NVIDIA GPU. Docker compose: [forge/docker-compose.yaml](forge/docker-compose.yaml) (Compose project `forge`)

- **MAC:** `88:d7:f6:13:14:3a`
- **GPU:** NVIDIA RTX 5060 Ti, 16 GB
- **Power:** Woken/slept by `bastion` cron; on-demand control via OpenClaw `forge-power.py`

**Wake-on-LAN has to be armed on forge itself.** Without it the NIC stops listening at
shutdown and every magic packet is dropped. The 5am wake failed this way from at least
March until 2026-09-23, while the log still said "WoL sent". Needs, on forge:

```bash
sudo nmcli c modify netplan-enp0s31f6 802-3-ethernet.wake-on-lan magic   # persists via netplan
sudo ethtool enp0s31f6 | grep Wake-on                                   # expect "Wake-on: g"
```

and in the BIOS (ASUS Maximus IX Hero): **ErP Ready = Disabled**, **Power On By PCI-E = Enabled**.
`wake-forge.sh` and `sleep-forge.sh` now confirm by ping that forge actually came up or
went down, and log `ERROR` in `~/logs/power.log` on bastion when it didn't.

| Container | Image | Port(s) | Notes |
|---|---|---|---|
| `ollama` | `ollama/ollama:latest` | 11434 | Local LLM inference, GPU-accelerated. One model resident at a time, held indefinitely (see below) |
| `model-loader` | `ollama/ollama:latest` | — | One-shot init: pulls the models below, then pre-loads `gpt-oss:20b` into VRAM. Exits non-zero if any step fails |
| `openclaw` | `ghcr.io/openclaw/openclaw:latest` | 18789, 18791 | AI agent gateway with Slack integration and Brave search |
| `open-webui` | `ghcr.io/open-webui/open-webui:main` | 8080 | Web UI for Ollama models |

**Models pulled by `model-loader`:**

| Model | Size | Source |
|---|---|---|
| `gpt-oss:20b` | 13 GB | ollama registry — the default, pre-loaded into VRAM at startup |
| `qwen2.5-coder:14b` | 9.0 GB | ollama registry |
| `hf.co/ServiceNow-AI/Apriel-1.6-15b-Thinker-GGUF:Q4_K_M` | 9.7 GB | Hugging Face — the upstream `ServiceNow-AI/Apriel-1.6-15b-Thinker` repo ships safetensors only, so the vendor's GGUF build is used |

#### One model in VRAM at a time — by design

All three models are kept on disk, but the GPU only ever holds one. This is deliberate, not a
workaround: each model is 9–14 GB against a 16 GB card, so two can never usefully coexist, and
letting ollama try would push layers onto the CPU and collapse throughput.

Two settings on the `ollama` service enforce it:

| Setting | Effect |
|---|---|
| `OLLAMA_MAX_LOADED_MODELS=1` | Ollama evicts the resident model before loading another, instead of co-locating them |
| `OLLAMA_KEEP_ALIVE=-1` | The resident model is never unloaded on idle — no cold starts between requests |

`gpt-oss:20b` is the intended resident model: `model-loader` pre-loads it at startup and
OpenClaw's `primary` model is `ollama/gpt-oss:20b`. Switching to another model is supported —
it simply evicts `gpt-oss:20b` and takes a one-time ~40 s load, after which the new model stays
resident until something else is requested.

```bash
docker exec ollama ollama ps    # what is resident right now
```

#### Context length is 16384

Ollama's own default lands on **4096** here. That is below OpenClaw's hard minimum of 16000, and
an agent turn fails outright with `Model context window too small (4096 tokens)` — so the server
has to be told explicitly. `OLLAMA_CONTEXT_LENGTH=16384` on the `ollama` service does that, and
OpenClaw's model definitions declare `contextWindow: 16384` / `maxTokens: 4096` to match.

Both numbers have to agree. If the server serves less than OpenClaw declares, prompts are
silently truncated; if it serves less than 16000, the agent refuses to run at all.

**16384 is the ceiling that keeps every model fully on the GPU.** Measured on the 16 GB card by
loading each model at a forced `num_ctx` and reading `ollama ps`:

| Model | at 16384 | at 32768 |
|---|---|---|
| `gpt-oss:20b` (MoE) | 14 GB, **100% GPU** | 14 GB, **100% GPU** |
| `qwen2.5-coder:14b` (dense) | 13 GB, **100% GPU** | 18 GB, 15% CPU / 85% GPU |
| `Apriel-1.6-15b-Thinker` (dense) | 12 GB, **100% GPU** | 17 GB, 9% CPU / 91% GPU |

`gpt-oss:20b` alone would run at 32768, but `OLLAMA_CONTEXT_LENGTH` is a single server-wide
default, so raising it past 16384 would push the two dense models into CPU offload. If you ever
change it, update `contextWindow` in both `openclaw.json` and `agents/main/agent/models.json`
together, and keep `maxTokens` well under it to leave room for input.

Because model ids must match exactly, both `openclaw.json` and the agent's `models.json` register
the Apriel model under its full `hf.co/...:Q4_K_M` id.

`openclaw` and `open-webui` depend on `ollama` being healthy, not on `model-loader` completing,
so a failed model pull doesn't keep the gateway down on boot.

#### Deploying changes

`./deploy.sh forge` pushes `forge/` to `/home/robby/homelab-deployed/forge/` and runs
[forge/apply.sh](forge/apply.sh) there:

| Source | Applied as |
|---|---|
| `forge/docker-compose.yaml` | `docker compose up -d` from the deployed copy |
| `forge/openclaw/` | copied into `/home/robby/openclaw-state/`, then `openclaw` is restarted |
| `forge/crontab` | installed as robby's crontab |
| `forge/scripts/*.sh` | run by cron straight from the deployed copy |

**The agent's live state is not in the repo or the deployed copy.** It lives at
`/home/robby/openclaw-state/`, bind-mounted by absolute path. Only a config subset is tracked —
`openclaw.json`, `models.json` and the power scripts — and deploy **copies those in and never
deletes**. Sessions, memory, `workspace/` (its own git repo), identity, devices and credentials
exist only there; an `rsync --delete` of the whole directory would destroy the agent.

That split is also why `sync-forge.sh` backs up `/home/robby/openclaw-state/` separately: it used
to be covered by the `Documents/` sync, and moving it out would otherwise have dropped it from the
weekly backup silently.

`apply.sh` restarts `openclaw` whenever one of those config files changes, because OpenClaw reads
its config at startup — without that, a config-only deploy would succeed and take effect never.

Because nothing running reads from a git working tree, `git switch` on forge no longer changes
live configuration. That was previously the cause of repeated silent reverts.

#### Agent credential scope

The `openclaw` container gets **only** `~/.ssh/openclaw_ed25519`, mounted as a single file —
not the whole `~/.ssh` directory:

```yaml
- /home/robby/.ssh/openclaw_ed25519:/home/node/.ssh/openclaw_ed25519:ro
```

`forge-power.py` and `plex-power.py` reference that key explicitly at every call site and pass
`StrictHostKeyChecking=no` / `UserKnownHostsFile=/dev/null`, so nothing else from `~/.ssh` is
needed. Mounting the whole directory additionally exposed `id_ed25519` — the key
`backup-to-tower.sh` uses for `root@192.168.0.212` — to a process running an LLM with web
access. Reading a file is the easiest thing for a prompt-injected agent to be talked into, so
that key should never be inside this container.

OpenClaw's own audit suggests `agents.defaults.sandbox.mode="all"` for small models with web
tools. **That is not usable here:** the container has no Docker socket and no Docker CLI
(`openclaw sandbox explain` reports `runtime: direct`), so it cannot create sandbox containers.
Granting it `/var/run/docker.sock` to fix that would hand the agent host-root-equivalent access,
which is worse than the risk being mitigated. Keeping credentials out of the container is the
useful control at this scale.

The key is further restricted by a **forced command** in each host's `authorized_keys`, so it can
only run the specific operations the agent needs — it cannot run arbitrary commands even though it
authenticates as `robby`:

| Host | Verbs the key may run |
|---|---|
| bastion | `wake-forge`, `wake-cinema` (Wake-on-LAN packets; no sudo), `health` (read-only checks, below) |
| forge | `shutdown` |
| cinema | `suspend` |

```
command="/home/robby/bin/openclaw-cmd wake-forge wake-cinema health",restrict ssh-ed25519 AAAA... openclaw
```

[scripts/openclaw-cmd](scripts/openclaw-cmd) is the wrapper; the verbs a host permits are its
arguments, and the requested verb arrives in `$SSH_ORIGINAL_COMMAND`. This matters because `robby`
is in bastion's `docker` group, so an unrestricted shell there is root-equivalent. `shutdown` is
deliberately **not** granted on bastion — it runs DNS and the vault for the whole house.

Install it on a new host with `install -m 755 scripts/openclaw-cmd ~/bin/openclaw-cmd`, then add
the forced command to that host's `authorized_keys`. Nothing deploys it, so reinstall by hand
after changing it.

`health` exists for OpenClaw's `server-log-monitor` cron job, which runs every 15 minutes and
posts to `#homelab-status` only when something is wrong. It runs a fixed set of read-only checks:
journal errors and critical kernel events from the last 15 minutes, stopped containers, and
filesystems at 80% or more. Each section prints `none` when clean and `CHECK FAILED` when the
check itself errors, so the agent can tell "healthy" from "couldn't look". The job lives in
`~/openclaw-state/cron/jobs.json` on forge, which is agent state rather than deployed config;
change it with `docker exec openclaw openclaw cron edit` while the gateway is running.

> **Your own key must be authorized separately.** `~/.ssh/config` on forge used to set
> `openclaw_ed25519` as the default identity for `Host *`, so admin SSH to bastion and cinema was
> silently using the *agent's* key. Once that key is restricted, only a separate admin key works —
> make sure `id_ed25519` is in each host's `authorized_keys` before restricting anything.

If the key set ever changes, re-verify with:

```bash
docker exec openclaw ls -l /home/node/.ssh/
docker exec openclaw python3 /home/node/.openclaw/scripts/forge-power.py status
docker exec openclaw python3 /home/node/.openclaw/scripts/plex-power.py status
```

---

### `vault` — 192.168.0.212 (Unraid) — Sunday 2am–5am

Cold storage backup server. Powered via smart plug on a fixed schedule (2:00am–5:00am Sunday).
Motherboard set to boot on power restore (WoL not supported). SSH user: `root`.

**Backup sources:**
- `bastion`: vaultwarden data, AdGuard config
- `forge`: `/home/robby/Documents/` (pushed directly from forge; this now includes the
  compose stack and openclaw state, which live in `Documents/Github/Homelab/forge/`)

---

## Deploying

One script deploys every host from any clone of this repo:

```bash
git pull
./deploy.sh --check bastion    # validate and list changed files, apply nothing
./deploy.sh bastion            # or: ./deploy.sh all
```

Hosts never run from a git clone. [deploy.sh](deploy.sh) rsyncs `<host>/` to
`/home/robby/homelab-deployed/<host>/` on that host and runs the host's `apply.sh` there.
A `git switch` in any clone, including one on the host itself, changes nothing running;
only a deploy does. Hosts need no GitHub access.

For each host:

1. Copy `<host>/` to a staging dir on the host.
2. Run `apply.sh --validate` against the staged copy (compose file, Caddyfile, …).
   A change that fails never reaches the live copy.
3. List the files that would change. `--check` stops here.
4. Replace the live copy with the staged one and run its `apply.sh`.

Guard rails:
- Deploys only when `HEAD` is `origin/main` and `<host>/` is clean (`--force` overrides),
  so what runs is what's merged.
- `.env` files are never pushed, and the ones on the host survive deploys.
- An unreachable host (forge and cinema sleep) fails fast and doesn't stop the others.

The host the script runs on is deployed locally; the others go over SSH as `robby`, so
the machine you deploy from needs key auth to each of them.

| Host | On `deploy.sh` | Runs from |
|---|---|---|
| `bastion` | yes | `/home/robby/homelab-deployed/bastion/` |
| `cinema` | not yet | managed by hand |
| `forge` | not yet | its git clone (see [Running the forge stack](#running-the-forge-stack)) |

forge is last on purpose. `forge/openclaw-state/` mixes config the repo owns with state
OpenClaw writes itself (logs, credentials, pairing), and a push would clobber that state.
That split has to be decided first.

---

## Repository Structure

One folder per server, each deployed by [deploy.sh](deploy.sh) (bastion so far; see
[Deploying](#deploying)).

```
Homelab/
├── README.md                         — this file
├── homelab.code-workspace
├── .secrets.env.example              — template for the GitHub PAT (see below)
├── deploy.sh                         — pushes <host>/ to that host and runs its apply.sh
├── scripts/
│   └── git-credential-homelab        — git credential helper; reads .secrets.env
│
├── bastion/                          — 192.168.0.10, deployed to ~/homelab-deployed/bastion/
│   ├── apply.sh                      — run on bastion by deploy.sh
│   ├── crontab                       — installed as robby's crontab
│   ├── scripts/                      — cron scripts, run from the deployed copy
│   │   ├── wake-forge.sh
│   │   ├── sleep-forge.sh
│   │   ├── sync-forge.sh
│   │   └── backup-to-tower.sh
│   └── vaultwarden/                  — compose & Caddy config for vaultwarden/caddy/adguard
│                                       (live data stays in /home/robby/vaultwarden/)
│
├── cinema/                           — 192.168.0.13 (plex)
│   ├── docker-compose.yml            — Compose project `plex`
│   └── .env.example                  — optional PLEX_CLAIM for first-time setup
│
└── forge/                            — 192.168.0.12 (runs from this folder)
    ├── docker-compose.yaml           — Compose project `forge` (ollama, openclaw, open-webui)
    ├── .env.example                  — copy to .env (gitignored) and fill in tokens
    ├── scripts/
    │   └── vaultwarden-version-check.sh  — @reboot; Slacks you when bastion's Vaultwarden is stale
    └── openclaw-state/               — openclaw runtime state, bind-mounted into the container
        ├── scripts/
        │   ├── plex-power.py         — cinema (plex) wake/sleep agent tool
        │   └── forge-power.py        — forge wake/sleep agent tool
        └── workspace/                — agent workspace (gitignored)
            └── TOOLS.md              — server names, IPs, and tool usage notes
```

### Running the forge stack

```bash
cd forge && docker compose up -d
```

The Compose project name is pinned to `forge` in `docker-compose.yaml`, so the
`forge_ollama_data` and `forge_openwebui_data` volumes stay attached even if this
directory is moved.

> **This working tree is live configuration.** `forge/openclaw-state/` is bind-mounted
> straight into the running `openclaw` container, and `forge/docker-compose.yaml` defines
> the running stack. A `git switch` or `git checkout` therefore **rewrites production
> config on disk** — checking out a branch without a change here silently reverts it, and
> the next container restart picks up the old values.
>
> In practice: keep branches that touch `forge/openclaw-state/` or `forge/docker-compose.yaml`
> short-lived, and after switching branches confirm what is actually on disk before
> restarting anything:
>
> ```bash
> grep -m1 contextWindow forge/openclaw-state/openclaw.json
> grep OLLAMA_CONTEXT_LENGTH forge/docker-compose.yaml
> docker exec openclaw node openclaw.mjs status | grep -i sessions   # what is loaded now
> ```
>
> This goes away once forge moves to `deploy.sh`. bastion no longer has the problem: it
> runs from its deployed copy, so a branch switch in its clone changes nothing running.

### Pushing to GitHub

The remote is HTTPS and authenticates with a fine-grained PAT kept out of git:

```bash
cp .secrets.env.example .secrets.env && chmod 600 .secrets.env   # then paste the token in
git config --local credential.helper '!scripts/git-credential-homelab'
```

`.secrets.env` is gitignored. The helper also accepts an already-exported
`$GITHUB_TOKEN` in preference to the file.

The helper only supplies credentials for push/fetch — the commit author comes from
git config. On a host with no global identity (e.g. `cinema`), set it for this clone:

```bash
git config --local user.name 'Reginald White'
git config --local user.email 'robbywhite.public@gmail.com'
```

