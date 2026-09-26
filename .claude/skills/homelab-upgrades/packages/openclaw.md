# OpenClaw (forge)

```json
{ "name": "openclaw", "tier": "watch", "host": "forge", "container": "openclaw",
  "compose": "forge/docker-compose.yaml",
  "image": "ghcr.io/openclaw/openclaw",
  "upstream": { "kind": "github-release", "repo": "openclaw/openclaw" },
  "tag_from_release": "strip-v" }
```

## Watch tier, and the awkward one

This was briefly filed as Drift on the grounds that its exposure is LAN-only. That was wrong. On
**blast radius** it is the most significant package here: the gateway binds to the LAN with
`tools.exec.security: "full"` and `ask: "off"`, which makes the gateway token unprompted remote code
execution, and the container holds `openclaw_ed25519`, which authenticates to three hosts. It also
sits at the centre of the homelab — the digester, the health reporting and the power scripts all run
through it.

It is also the only Watch package that ships fast — six or seven releases a month, against under
three a month for the other three combined. So **the target is bounded staleness, not currency.**
Take a scheduled hop on a regular cadence and keep the gap in months rather than trying to track
releases. It had reached six months behind when this file was written, on a tag called `latest`,
which is the outcome to avoid.

## Hazard

- The agent's live state — sessions, memory, identity, credentials, cron jobs — lives at
  `/home/robby/openclaw-state`, outside the repo and outside the deployed copy. A major version can
  migrate or invalidate it.
- `openclaw.json` is **deploy-managed and rewritten at runtime.** `apply.sh` copies the tracked file
  over the live one whenever the two differ, and OpenClaw itself rewrites the live copy (see
  `meta.lastTouchedAt` / `lastTouchedVersion`). If a new version changes the config schema, the
  deploy will keep forcing a stale-shaped config over what the new version writes, every time.
  **This is the main hazard of an OpenClaw upgrade and it is specific to how this repo deploys it.**
- The gateway token comes only from `OPENCLAW_GATEWAY_TOKEN` in `forge/.env`. Never let a version
  bump reintroduce a literal token into the tracked `openclaw.json`.

## Contracts

Three things outside this container depend on its behaviour. Check all three against the target
release **before** bumping the tag.

1. **`forge/scripts/health-digest.py` parses the CLI.** It runs
   `docker exec openclaw node openclaw.mjs message read --channel slack --target channel:<id>
   --after <ts> --limit 200 --json` and reads `json.loads(out)["payload"]["messages"]`, then
   `app_id`, `ts` and `text` per message. It also calls `openclaw agent --json` for digest prose.
   **If that output shape moved, the digester breaks and all health alerting stops.** The saving
   grace is `bastion/scripts/digest-watchdog.py`: no digest within 26 hours and it tags you
   directly. So this fails loudly rather than silently — but verify the shape first.
2. **The container's command line.** `docker-compose.yaml` runs
   `node openclaw.mjs gateway --allow-unconfigured --bind lan`. A renamed or removed flag means the
   container will not start.
3. **`agents/main/agent/models.json` and `contextWindow`.** Both `openclaw.json` and `models.json`
   are deploy-managed and declare `contextWindow: 16384`. OpenClaw enforces a minimum around 16000;
   ollama's own default lands below it and stops the agent running at all. If the model config
   schema changed, both files need updating together.

## Before upgrading

- `openclaw-state/` goes to vault weekly via `bastion/scripts/sync-forge.sh` in the Sunday window.
  **Check that backup's age before starting** — this is the rollback path for agent state.
- Note what `nativeSkills` and the skills directory layout look like in the target release. Skills
  load from `~/.openclaw/skills` inside the container, which is the state-dir bind mount at
  `/home/robby/openclaw-state/skills`. Newer releases parse skill frontmatter as real YAML; the
  version this repo has pinned falls back to a single-line-only parser, which is why every skill
  here is written with single-line frontmatter keys.
- This is the largest backlog of any package here. **Plan intermediate hops** rather than crossing
  many months at once, and expect the release notes not to fit in forge's own context — read them on
  the laptop.

## Verify after

```bash
ssh robby@192.168.0.12 "docker compose -f /home/robby/homelab-deployed/forge/docker-compose.yaml ps"
ssh robby@192.168.0.12 "docker logs --tail 60 openclaw"
```

Then exercise each contract, in this order:

```bash
# 1. the CLI shape the digester depends on
ssh robby@192.168.0.12 "docker exec openclaw node openclaw.mjs message read --channel slack \
  --target channel:C0C3XSNE71T --limit 3 --json" | head -40
# 2. the digester itself, without posting
ssh robby@192.168.0.12 "/home/robby/homelab-deployed/forge/scripts/health-digest.py --digest --dry-run"
```

Finally, say something to the bot in Slack and confirm it answers — the gateway starting does not
prove the Slack channel is connected.

## Rollback

Restore the previous tag and redeploy. If agent state was migrated, restore `openclaw-state/` from
the vault backup as well — a rolled-back binary may not read a migrated state directory.
