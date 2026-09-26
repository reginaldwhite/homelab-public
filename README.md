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

#### GDM takes the GPU away from the running container

**Observed 2026-09-24.** forge woke on schedule at 05:00, docker started at 05:00:40, and ollama
found the RTX 5060 Ti at 05:00:46. At 05:05:43 the GDM login screen came up and unloaded the
nvidia kernel modules — the journal shows `gdm-session-worker: rmmod: ERROR: Module nvidia is in
use by: nvidia_uvm`. The first model load at 06:36 then fell back to `GPULayers:[]` and ran
`gpt-oss:20b` **entirely on the CPU**.

Nothing looked broken. The container stayed up and healthy, `nvidia-smi` on the *host* still
worked; only `docker exec ollama nvidia-smi` failed, with `Failed to initialize NVML: Unknown
Error`. What it looked like from outside was the agent no longer answering Slack, because a 20B
model on CPU takes minutes per reply instead of ~4 seconds.

`docker restart ollama` restores it. [forge/scripts/gpu-guard.sh](forge/scripts/gpu-guard.sh)
checks every 15 minutes during waking hours and does exactly that, then reloads the model so the
next request isn't a cold start. It catches two states: the container unable to reach the driver
at all, and a model left resident on the CPU because it was loaded while the driver was
unreachable — which `OLLAMA_KEEP_ALIVE=-1` would otherwise pin there forever. Every repair is
reported to `#homelab-logs`, because a self-healing loop that heals silently just hides a
recurring fault.

**The guard is a mitigation. The cure is not to run a graphical login manager on a headless
server:**

```bash
sudo systemctl set-default multi-user.target && sudo systemctl disable gdm3
```

That removes the cause outright, along with the i915 hotplug churn and most of the
desktop-session noise `openclaw-cmd`'s `NOISE` list exists to filter. It is left as a decision
because it changes what you get when you sit down at the machine — a text console, with
`sudo systemctl start gdm3` to bring the desktop up on demand.

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

It posts a `vaultwarden-version` report to `#homelab-logs` like every other producer, and the
digester escalates when it isn't `ok`. It used to DM Robby directly, which made it one more
thing deciding severity for itself.

A check that *fails* still reports — an unreachable bastion or GitHub becomes `status: error`
rather than being silently skipped, since a silently broken notifier is the exact failure being
guarded against. It also no longer needs the OpenClaw container: it posts to Slack directly, so
the ten-minute wait for the container at boot is gone, replaced by a bounded wait for the
network, which is the real dependency. To re-verify delivery at any time:

```bash
forge/scripts/vaultwarden-version-check.sh --test      # posts a sample report
forge/scripts/vaultwarden-version-check.sh --dry-run   # prints it, sends nothing
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
| 7:00am daily | Report bastion's own health to `#homelab-logs` (`health-report.py`) |
| 9:00am daily | Check the digest is still being posted (`digest-watchdog.py`) |
| 2:00am Sunday | Wake `vault` via WoL (`wake-vault.sh`) |
| 2:30am Sunday | Push bastion data → `vault` (`backup-to-tower.sh`) |
| 2:45am Sunday | Wake `forge` for backup |
| 3:00am Sunday | Sync forge data → `vault` (`sync-forge.sh`) |
| 3:30am Sunday | Report on what the backup left on `vault` (`vault-report.py`) |
| 3:45am Sunday | Sleep `forge` after backup |
| 4:00am Sunday | Shut down `vault` (`sleep-vault.sh`) |

Scripts: [bastion/scripts/](bastion/scripts/). Both the crontab and the scripts are
installed by `./deploy.sh bastion`, so edit them here rather than with `crontab -e` on bastion.

#### Self-reports

Every host posts a structured report about itself to `#homelab-logs`, with no agent involved.

| Host | When | Script |
|---|---|---|
| bastion | 7:00am daily | [shared/health-report.py](shared/health-report.py) |
| forge | 7:10am daily | [shared/health-report.py](shared/health-report.py) |
| cinema | every 6h *while awake* | [shared/health-report.py](shared/health-report.py) |
| vault | Sunday 3:30am, **from bastion** | [bastion/scripts/vault-report.py](bastion/scripts/vault-report.py) |

`health-report.py` lives once, in [shared/](shared/), and is merged into every host's
`scripts/` at deploy time. It works out which host it is from the hostname rather than a
per-host constant, so one file serves all three.

cinema is suspended most of the time and cron does not replay jobs missed while suspended, so
it reports whenever it happens to be awake. That is the honest semantic for that host: absence
is normal and is never escalated.

It reports **facts and does not decide what is urgent**. Severity is judged once, by the digester
on forge, which can correlate across hosts and across days in a way no single reporter can. The
full design and its trade-offs are in [docs/health-digest.md](docs/health-digest.md).

```
<@openclaw> bastion health: ok (0 findings)
```json
{"host":"bastion","kind":"health","status":"ok","ts":"…",
 "findings":[], "metrics":{"fs_max_pct":68,"journal_errors":0,"stopped_containers":0}}
```
```

`metrics` exists so trends are possible at all: a digester can only notice "climbing 3% a day"
if the number is in the report rather than buried in prose.

It runs the same `health` verb the agent's monitor runs rather than reimplementing the checks, so
the two can never disagree about what healthy means, and it refuses to call a truncated run
healthy — fewer than five sections is reported as `status: error`.

**Two conditions bypass the digester** and tag Robby directly in `#homelab-status`, because they
cannot wait for forge to wake up: any filesystem at **95%** or more, and the weekly backup to
vault having **failed** on its last run. Both are deterministic and rare. Everything else waits
for the digester.

#### Digest watchdog

[bastion/scripts/digest-watchdog.py](bastion/scripts/digest-watchdog.py) runs at 9:00am and tags
Robby if no digest has appeared in `#homelab-status` for 26 hours.

This is not optional. Routing alerts through the agent makes them depend on **forge** — off
11pm–5am by design, and down for two months in mid-2026. A digester that has stopped cannot
report that it has stopped, and silence is not an alert. Apart from the two catastrophe
conditions above, this is the only machine-to-human tag left in the system.

It needs to *read* `#homelab-status`, and a webhook cannot read, so bastion keeps
`SLACK_BOT_TOKEN` even if posting later moves to webhooks.
[docs/slack-credentials.md](docs/slack-credentials.md) is the plan for removing that token from
every host that only needs to post, and what it would take to free bastion too.

#### Credentials

`.env` on the deployed copy, which `deploy.sh` never pushes and never overwrites — see
[bastion/.env.example](bastion/.env.example):

```bash
umask 077 && vi /home/robby/homelab-deployed/bastion/.env
```

Re-verify delivery at any time, which is worth doing after touching either script since both are
silent for long stretches:

```bash
/home/robby/homelab-deployed/bastion/scripts/health-report.py --test
/home/robby/homelab-deployed/bastion/scripts/digest-watchdog.py --test
```

Both also take `--dry-run`. A post Slack rejects is written to the script's log in `~/logs/` with
the reason and exits non-zero, rather than being swallowed.

---

### `cinema` — 192.168.0.13 (Ubuntu) — On Demand

Dedicated Plex Media Server. Woken via WoL by the OpenClaw agent on `forge` when needed,
and by `deploy.sh` itself when a deploy finds it asleep.
Docker compose: [cinema/docker-compose.yml](cinema/docker-compose.yml) (Compose project `plex`)

- **MAC:** `d0:17:c2:dd:c1:c2`

| Container | Image | Port(s) | Notes |
|---|---|---|---|
| `plex` | `plexinc/pms-docker:latest` | host network (32400) | Config in `/root/plex/config`, media in `/mnt/storage/plex` → `/data` |

The stack was first started with `sudo`, so the config/transcode bind mounts live under
`/root/plex/` and the compose file uses absolute paths to keep pointing at them.
`PLEX_CLAIM` is only needed when claiming a fresh server — put it in `cinema/.env`.

**Those `/root/` paths do not require sudo to deploy.** They are bind mounts, opened by
`dockerd` (which is root) and never by `apply.sh`, and `robby` is in cinema's `docker`
group — so `docker compose` runs unprivileged. The passwordless sudo grant this migration
was expected to need was never needed; cinema's only sudo rule stays
`NOPASSWD: /bin/systemctl suspend`, for the agent's `suspend` verb.

[cinema/apply.sh](cinema/apply.sh) refuses to deploy when `/mnt/storage` is not mounted.
Without that check `dockerd` would create an empty `/mnt/storage/plex` on the root disk and
Plex would start on an empty library — a failure that looks exactly like losing it.

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
| bastion | `wake-forge`, `wake-cinema`, `wake-vault` (Wake-on-LAN packets; no sudo), `health` (read-only checks, below) |
| forge | `shutdown` |
| cinema | `suspend`, `health` (read-only checks, below) |

```
command="/home/robby/bin/openclaw-cmd wake-forge wake-cinema health",restrict ssh-ed25519 AAAA... openclaw
```

[shared/openclaw-cmd](shared/openclaw-cmd) is the wrapper; the verbs a host permits are its
arguments, and the requested verb arrives in `$SSH_ORIGINAL_COMMAND`. This matters because `robby`
is in bastion's `docker` group, so an unrestricted shell there is root-equivalent. `shutdown` is
deliberately **not** granted on bastion — it runs DNS and the vault for the whole house.

`deploy.sh` installs it to `~/bin/openclaw-cmd` on every host, so a change only needs a deploy.
Adding a *new* host still means putting the forced command in that host's `authorized_keys` by
hand — that line is what constrains the key, and it is deliberately not something a deploy can
rewrite. The wrapper is installed to `~/bin` rather than run from the deployed copy so that a
host which has never been deployed to still has a working verb, instead of an agent key that
fails closed.

`health` is the read-only check every host's reporter runs. Since the move to
[reported facts plus one digester](docs/health-digest.md), **nothing tags Robby except the
digester**, plus two catastrophe conditions on bastion and the digest watchdog.

Both agent-driven log monitors are therefore **retired** — disabled rather than deleted, so
`openclaw cron enable <id>` brings either back:

| Job | Replaced by |
|---|---|
| `server-log-monitor` | bastion self-reports; a host that goes quiet for 26h is escalated by absence detection |
| `cinema-log-monitor` | cinema self-reports whenever it is awake |

Both polled a host from forge and decided for themselves whether to interrupt. That judgement
now happens once, in the digester. `cinema-log-monitor` also produced a twice-daily
`💤 cinema: asleep` confirming the check had run; that positive signal is the one thing lost,
and it is an acceptable trade because cinema's absence is never alerted on either way.

`health` runs a fixed set of read-only checks: journal errors and
critical kernel events from the last 14 hours (the longer gap between runs), stopped
containers, filesystems at 80% or more, and the outcome of the weekly backup to vault. Known-harmless journal entries (GDM's login-screen
assertion, BIOS CPU-feature notes at boot, desktop-session apps, keyring handling at login, and
`sudo -n` probes) are filtered by the `NOISE` list in the script, so a healthy bastion reads
`all clear`. Keep new patterns just as narrow: name the process and the exact message.

`EXPECTED_EXITED` does the same job for containers whose exit is the end of their work rather
than a fault — `model-loader` pulls models once at boot and stops by design, so without it forge
would report a finding every single day and `warn` would stop meaning anything. Name exact
containers there, never patterns that could hide a real service stopping. Each section prints `none` when clean and `CHECK FAILED` when the
check itself errors, so the agent can tell "healthy" from "couldn't look".

The backup section reads bastion's `~/logs/backup.log` and reports a last run that failed, or a
last success older than 8 days. It exists because the weekly backup to vault failed **every
week from May to September 2026** and nothing ever said so — the logs were written correctly and
simply never read. On a host with no backup log (only bastion pushes to vault) it prints `none`.

The jobs live in `~/openclaw-state/cron/jobs.json` on forge, which is agent state rather than
deployed config; change them with `docker exec openclaw openclaw cron edit` while the gateway is
running.

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

Cold storage backup server. SSH user: `root`.

**Powered by bastion over Wake-on-LAN, not by the smart plug** — woken Sunday 2:00am, shut down
4:00am, and wakeable on demand. The plug stays installed and switched on as a manual override,
but nothing schedules it.

Getting WoL working needed three things on this board, and the middle one is why it looked
impossible for a while: the NIC armed (`ethtool wol g`), **the NIC's PCIe root port allowed to
raise a wake** (`P0P6`/`P0P9` in `/proc/acpi/wakeup`), and the kernel's per-device flag set.
With the root port disabled, everything else reads as correctly configured and nothing ever
happens. Full write-up, including the boot race that made the arming unreliable, in
[docs/vault-power.md](docs/vault-power.md).

**Backup destinations,** all under `/mnt/user/backups/`:

| Path | Source | Pushed by |
|---|---|---|
| `vaultwarden/` | bastion's `~/vaultwarden/` — vw-data, Caddyfile, compose | `backup-to-tower.sh` |
| `vaultwarden/agh-conf/`, `agh-work/` | bastion's AdGuard state, staged through a root container | `backup-to-tower.sh` |
| `forge/Documents/` | forge's `~/Documents/` — this repo and everything developed there | `sync-forge.sh` |
| `forge/openclaw-state/` | forge's `~/openclaw-state/` — agent memory, sessions, credentials | `sync-forge.sh` |

**vault is deliberately not a `deploy.sh` host,** and that is not a to-do:

- There is nothing to deploy. No Docker daemon runs on it, it has no crontab of ours, and
  its own configuration lives on the Unraid flash and is managed from the web GUI.
- Its root filesystem is **RAM** (`rootfs`, ~8.8 GB). Anything under `/root` or `/home` is
  gone at the next boot, and it reboots every week when the smart plug cycles.
- Its flash (`/boot`) is **vfat mounted `fmask=0177`**, so a file there can never carry an
  executable bit. `chmod +x` succeeds and the file still refuses to run. A deployed
  `apply.sh` could neither survive on one filesystem nor execute on the other.

So vault is managed *from bastion*, which is always on and is the host that pushes to it.
`./deploy.sh vault` prints that reasoning rather than failing obscurely.

The backup scripts create their own destination paths (`rsync --mkpath`), so a fresh or
re-created share needs no manual preparation.

**vault is reported on by bastion,** not by itself —
[bastion/scripts/vault-report.py](bastion/scripts/vault-report.py), Sunday 3:30am, after both
syncs and before the plug cuts power. The RAM root and vfat flash above already rule out
installing anything, and it has no `python3` at all. But the decisive reason is different: a
reporter on vault could not report **vault failing to power on**. Run from bastion, that becomes
a report with `status: error` instead of silence.

It reports the array state (`mdState`, `fsState`, disabled/invalid/missing disks), the last
parity check's error count and exit code, per-disk `DISK_OK` status, `/mnt/user` usage, and how
old the newest file under each backup source is — so a backup that silently stopped arriving is
visible as a number rather than something you have to go and look for.

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
- `.env` and `.secrets.env` are never pushed, and the ones on the host survive deploys.
- A host that is asleep is **woken** (below) rather than skipped.
- A host that still can't be reached fails fast and doesn't stop the others.

The host the script runs on is deployed locally; the others go over SSH as `robby`, so
the machine you deploy from needs key auth to each of them.

| Host | On `deploy.sh` | Runs from |
|---|---|---|
| `bastion` | yes | `/home/robby/homelab-deployed/bastion/` |
| `cinema` | yes | `/home/robby/homelab-deployed/cinema/` |
| `forge` | yes | `/home/robby/homelab-deployed/forge/` |
| `vault` | no, by design | nothing to deploy — see [`vault`](#vault--1921680212-unraid--sunday-2am5am) |

### Waking hosts on deploy

forge sleeps overnight and cinema is asleep most of the time, so "deploy everything" used
to mean "deploy whatever happens to be awake". When a host doesn't answer SSH, `deploy.sh`
now sends it a Wake-on-LAN magic packet and polls SSH for up to 180 seconds:

| Host | MAC | Wakeable |
|---|---|---|
| `forge` | `88:d7:f6:13:14:3a` | yes |
| `cinema` | `d0:17:c2:dd:c1:c2` | yes |
| `bastion` | — | always on |
| `vault` | `e0:cb:4e:9d:02:6a` (+ `…f4:d6`) | yes — but it is not a deploy target, so bastion wakes it on a schedule instead; see [docs/vault-power.md](docs/vault-power.md) |

The packet is layer 2, so this only works from a machine on the same LAN — true of every
host here. A woken host is **left running**, because sleeping it again could cut off
whoever is using it; the run prints which hosts it woke. forge shuts itself down at 11pm
via bastion's cron anyway; sleep cinema with `plex-power.py sleep`. Pass `--no-wake` to
opt out, e.g. so a scripted deploy can't power on the living-room Plex box at 3am.

`apply.sh` waits for `dockerd` on each host, since a just-woken machine accepts SSH well
before Docker is ready.

### Deploying from another machine

Nothing here is specific to the machine you deploy from — `deploy.sh` treats a host as local
only when its IP is one of yours, and otherwise goes over SSH. Running it from a laptop deploys
every host, forge included, the same way.

This matters because forge does not need a desktop: see
[GDM takes the GPU away](#gdm-takes-the-gpu-away-from-the-running-container).

**Set this up while you still have working access to the current machine, and verify it before
relying on it.**

1. **Tools:** `git`, `bash`, `ssh`, `rsync`. On macOS check `rsync --version` first — the system
   copy is ancient; `brew install rsync` if so.
2. **SSH key.** Generate one on the new machine and add its public key to
   `~/.ssh/authorized_keys` on bastion, forge and cinema, as `robby`. Append it — do not
   overwrite: those files also hold the agent's forced-command line, and replacing them breaks
   the agent's power and health verbs.

   ```bash
   ssh-keygen -t ed25519 -C "$(hostname)"
   ssh-copy-id -i ~/.ssh/id_ed25519.pub robby@192.168.0.10   # and .12, .13
   ```

   `ssh-copy-id` fails against a host whose only authorized key is a forced command, because its
   probe runs that command — if that happens, append the key by hand.
3. **GitHub PAT.** `cp .secrets.env.example .secrets.env && chmod 600 .secrets.env`, then
   `git config --local credential.helper '!scripts/git-credential-homelab'`. **Issue a separate
   fine-grained token for each machine** rather than copying one around, so a machine can be
   revoked without breaking the others.
4. **Git identity,** since a fresh clone inherits nothing:
   `git config --local user.name '...'` and `user.email`.

Then verify, before you need it:

```bash
for h in 10 12 13; do ssh -o BatchMode=yes robby@192.168.0.$h hostname; done
./deploy.sh --check all        # validates every host, changes nothing
```

**What you do *not* need to copy:** `.env` files. They hold each host's secrets, live only on
that host, and are never pushed — `deploy.sh` excludes them, so they survive every deploy and a
new machine never sees them.

**Wake-on-LAN only works from the LAN.** The magic packet is layer 2, so deploying from outside
the house cannot wake forge or cinema; the deploy just fails after 180s. bastion is always on and
can do it for you:

```bash
ssh robby@192.168.0.10 /home/robby/homelab-deployed/bastion/scripts/wake-forge.sh
```

**Nothing is hand-installed any more** except the forced-command line in each host's
`authorized_keys`, and the `.env` files. Scripts, crontabs and the agent's config all arrive by
deploy, so a second machine needs no setup on the hosts beyond step 2.

---

## Repository Structure

One folder per server, each deployed by [deploy.sh](deploy.sh) — bastion, cinema and
forge; see [Deploying](#deploying). vault has no folder because nothing is deployed to it.

```
Homelab/
├── README.md                         — this file
├── homelab.code-workspace
├── .secrets.env.example              — template for the GitHub PAT (see below)
├── deploy.sh                         — pushes <host>/ to that host and runs its apply.sh
├── scripts/
│   └── git-credential-homelab        — git credential helper; reads .secrets.env
│
├── shared/                           — merged into every host's scripts/ by deploy.sh
│   ├── health-report.py              — each host reports itself to #homelab-logs
│   └── openclaw-cmd                  — the agent's forced command; installed to ~/bin
│
├── bastion/                          — 192.168.0.10, deployed to ~/homelab-deployed/bastion/
│   ├── apply.sh                      — run on bastion by deploy.sh
│   ├── crontab                       — installed as robby's crontab
│   ├── .env.example                  — copy to .env on the host: Slack creds for the self-check
│   ├── scripts/                      — cron scripts, run from the deployed copy
│   │   ├── wake-forge.sh
│   │   ├── sleep-forge.sh
│   │   ├── sync-forge.sh
│   │   ├── backup-to-tower.sh
│   │   ├── wake-vault.sh             — Sun 2:00am; WoL, then waits for its array
│   │   ├── sleep-vault.sh            — Sun 4:00am; arms WoL, verifies, powers off
│   │   ├── vault-report.py           — Sun 3:30am; reports on vault, which cannot
│   │   └── digest-watchdog.py        — 9am; tags Robby if the digest goes quiet
│   └── vaultwarden/                  — compose & Caddy config for vaultwarden/caddy/adguard
│                                       (live data stays in /home/robby/vaultwarden/)
│
├── cinema/                           — 192.168.0.13, deployed to ~/homelab-deployed/cinema/
│   ├── apply.sh                      — run on cinema by deploy.sh
│   ├── crontab                       — installed as robby's crontab
│   ├── (scripts/ is created at deploy time from shared/)
│   ├── docker-compose.yml            — Compose project `plex`
│   └── .env.example                  — optional PLEX_CLAIM for first-time setup
│
└── forge/                            — 192.168.0.12, deployed to ~/homelab-deployed/forge/
    ├── apply.sh                      — run on forge by deploy.sh
    ├── crontab                       — installed as robby's crontab
    ├── docker-compose.yaml           — Compose project `forge` (ollama, openclaw, open-webui)
    ├── .env.example                  — copy to .env (gitignored) and fill in tokens
    ├── scripts/
    │   ├── vaultwarden-version-check.sh  — @reboot; reports if bastion's Vaultwarden is stale
    │   ├── gpu-guard.sh              — every 15min; restarts ollama when it loses the GPU
    │   └── health-digest.py          — hourly triage + daily digest of #homelab-logs
    └── openclaw/                     — the config subset deploy copies into ~/openclaw-state/
        ├── openclaw.json             — gateway, models, channels
        ├── models.json               — per-model context windows
        └── scripts/
            ├── plex-power.py         — cinema (plex) wake/sleep agent tool
            └── forge-power.py        — forge wake/sleep agent tool
```

### Running the forge stack

forge runs from `/home/robby/homelab-deployed/forge/`, like every other deployed host:

```bash
./deploy.sh forge
```

The Compose project name is pinned to `forge` in `docker-compose.yaml`, so the
`forge_ollama_data` and `forge_openwebui_data` volumes stay attached even if the folder
moves.

> **This working tree is no longer live configuration.** It used to be: the stack ran
> straight out of the git tree, so a `git switch` rewrote production config on disk, and
> it silently reverted OpenClaw's context window four separate times. That is what the
> deployed-copy model exists to prevent — the live copy has no branch to switch. Config
> the repo owns is copied into `/home/robby/openclaw-state/` by
> [forge/apply.sh](forge/apply.sh), which never deletes from it, so the agent's sessions,
> memory, credentials and workspace are untouched by a deploy.
>
> Because OpenClaw caches its config at startup, `apply.sh` restarts the container
> whenever one of those files actually changed — otherwise a config-only deploy would
> look successful and change nothing.

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

