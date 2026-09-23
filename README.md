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

General services and scheduling hub. Docker compose: [bastion/vaultwarden/docker-compose.yml](bastion/vaultwarden/docker-compose.yml)

| Container | Image | Port(s) | Notes |
|---|---|---|---|
| `vaultwarden` | `vaultwarden/server:1.37.3` | 8080→80 | Bitwarden-compatible vault at `vault.robbywhite.net`, signups disabled. Version pinned — see below |
| `caddy` | `caddy:latest` | 80, 443 | Reverse proxy / TLS termination |
| `adguardhome` | `adguard/adguardhome` | host network | DNS-level ad & tracker blocking |

#### Vaultwarden version is pinned

Bitwarden client compatibility is version-sensitive: clients refuse to sync against a server
that is too old, and the failure surfaces as an opaque decoding error rather than a version
warning. Vaultwarden **1.37.2+** is required for clients **2026.8.0** and newer.

`:latest` is misleading here — it only moves when something runs `docker compose pull`, which
nothing does on a schedule, so the server sat on 1.35.3 for seven months while the tag said
"latest". The tag is pinned so the deployed version is visible in git.

To upgrade: bump the tag in [bastion/vaultwarden/docker-compose.yml](bastion/vaultwarden/docker-compose.yml),
copy it to `/home/robby/vaultwarden/` on bastion, then:

```bash
# back up first — Vaultwarden's DB migrations are not reversible
docker compose stop vaultwarden
docker run --rm -v /home/robby/vaultwarden/vw-data:/d:ro -v /home/robby/vw-backups:/out \
  alpine tar czf /out/vw-data-$(date +%F).tgz -C /d .
docker compose pull vaultwarden && docker compose up -d vaultwarden
docker logs vaultwarden | grep Version
```

Backups land in `/home/robby/vw-backups/` on bastion.

#### Staleness alert

Pinning means nothing upgrades on its own, so
[forge/scripts/vaultwarden-version-check.sh](forge/scripts/vaultwarden-version-check.sh)
compares the tag bastion is actually running against the newest GitHub release and DMs
you in Slack through OpenClaw when it's behind. It reports the *deployed* tag
(`docker inspect`), not the one the repo intends, so drift between the two shows up too.

It runs on **forge**, from the user crontab at boot:

```cron
@reboot /home/robby/Documents/Github/Homelab/forge/scripts/vaultwarden-version-check.sh
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

**Cron schedule** (controls all other servers):

| Time | Job |
|---|---|
| 5:00am daily (Mon–Sat) | Wake `forge` via WoL |
| 5:00am Sunday | Wake `forge` via WoL (after backup window) |
| 11:00pm daily | Shut down `forge` via SSH |
| 2:30am Sunday | Push bastion data → `vault` (`backup-to-tower.sh`) |
| 2:45am Sunday | Wake `forge` for backup |
| 3:00am Sunday | Sync forge data → `vault` (`sync-forge.sh`) |
| 3:45am Sunday | Sleep `forge` after backup |

Scripts: [bastion/scripts/](bastion/scripts/)

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

---

### `vault` — 192.168.0.212 (Unraid) — Sunday 2am–5am

Cold storage backup server. Powered via smart plug on a fixed schedule (2:00am–5:00am Sunday).
Motherboard set to boot on power restore (WoL not supported). SSH user: `root`.

**Backup sources:**
- `bastion`: vaultwarden data, AdGuard config
- `forge`: `/home/robby/Documents/` (pushed directly from forge; this now includes the
  compose stack and openclaw state, which live in `Documents/Github/Homelab/forge/`)

---

## Repository Structure

One folder per server. Everything lives on `forge` at
`/home/robby/Documents/Github/Homelab/`; the `bastion/` files are the source-of-truth
copies of what is deployed to `192.168.0.10`.

```
Homelab/
├── README.md                         — this file
├── homelab.code-workspace
├── .secrets.env.example              — template for the GitHub PAT (see below)
├── scripts/
│   └── git-credential-homelab        — git credential helper; reads .secrets.env
│
├── bastion/                          — 192.168.0.10 (deployed via scp)
│   ├── scripts/                      — cron scripts
│   │   ├── wake-forge.sh
│   │   ├── sleep-forge.sh
│   │   ├── sync-forge.sh
│   │   └── backup-to-tower.sh
│   └── vaultwarden/                  — compose & Caddy config for vaultwarden/caddy/adguard
│                                       (vw-data/ is live data, gitignored)
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

