# Homelab

## Network

| Device | IP |
|---|---|
| ATT Fiber Router | `192.168.0.1` |
| `bastion` (file) | `192.168.0.10` |
| `cinema` (plex-7700t) | `192.168.0.13` |
| `forge` (7700k) | `192.168.0.12` |
| `vault` (tower) | `192.168.0.212` |

Subnet: `192.168.0.0/24`

---

## Servers

### `bastion` — 192.168.0.10 (Ubuntu) — Always On

General services and scheduling hub. Docker compose: [bastion/vaultwarden/docker-compose.yml](bastion/vaultwarden/docker-compose.yml)

| Container | Image | Port(s) | Notes |
|---|---|---|---|
| `vaultwarden` | `vaultwarden/server:latest` | 8080→80 | Bitwarden-compatible vault at `vault.robbywhite.net`, signups disabled |
| `caddy` | `caddy:latest` | 80, 443 | Reverse proxy / TLS termination |
| `adguardhome` | `adguard/adguardhome` | host network | DNS-level ad & tracker blocking |

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

- **MAC:** `d0:17:c2:dd:c1:c2`

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
└── forge/                            — 192.168.0.12 (runs from this folder)
    ├── docker-compose.yaml           — Compose project `forge` (ollama, openclaw, open-webui)
    ├── .env.example                  — copy to .env (gitignored) and fill in tokens
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

