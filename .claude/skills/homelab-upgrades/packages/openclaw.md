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
3. **The tool-policy keys in `openclaw.json`.** `tools.deny: ["group:web","browser"]`,
   `tools.elevated.enabled: false` and `tools.fs.workspaceOnly: true` are deliberate security
   settings, not defaults. If a release renames or restructures any of them, the deny silently stops
   applying and the agent quietly regains web tools and unconfined filesystem access — with nothing
   failing to announce it. After any upgrade, run `docker exec openclaw node openclaw.mjs security
   audit` and confirm it does **not** report open web tools or elevated being enabled.
4. **`contextWindow` and `params.num_ctx` in `openclaw.json`.** Every model under
   `models.providers.ollama.models` declares `contextWindow: 16384`, and since the 2026.9 line
   also `params.num_ctx: 16384` (doctor added it). OpenClaw enforces a minimum around 16000;
   ollama's own default lands below it and stops the agent running at all. `agents/main/agent/
   models.json` is **generated** by the agent from `openclaw.json` and the loaded plugins — it is
   not tracked, not deployed, and not where anything is configured.
5. **Exec approvals live in SQLite, not `exec-approvals.json`.** The 2026.9 startup migration
   imported the JSON file into shared SQLite state and removed it. `autoAllowSkills` is still on
   in there and `security audit` now flags it; it has to be changed through the `openclaw` CLI
   or Control UI rather than by editing a file. It is false, and since 2026-09-28 exec is
   `allowlist` on both layers: `tools.exec.mode` in `openclaw.json` and the host document kept
   at `forge/openclaw/exec-approvals.json5` (applied with `approvals set --stdin`, see the
   comment in that file). A new agent-runnable script means a new entry there, pinned by
   `argPattern`; a skill that tells the agent to run anything else will simply be denied.
   After an upgrade, `approvals get` must still show `allowlist` on both sides — a release
   that migrates the document could reset it.

   **A deploy does not apply it.** Adding a verb to a script, committing the matching entry
   and deploying leaves the stored policy behind, and the new verb is denied as an allowlist
   miss — `sauna.py next|runs|phase|label` shipped on 2026-10-15 and were dead all day for
   exactly this reason, with the agent telling Robby to run the command himself. Since then
   `forge/apply.sh` compares the document with `approvals get --json` at the end of every
   deploy and prints the entries that are not live, with the command to apply them. Read the
   last lines of a forge deploy.
6. **Skills belong in `openclaw-state/skills/`, nothing else.** OpenClaw also reads a `skills/`
   directory inside the workspace, and a copy there shadows the deployed one. The March 2026
   install left `openclaw-state/workspace/skills/plex-power/` behind, still carrying the
   `bash command:"…"` notation that was removed from the real skill in 5cf7db0 — on 2026-10-15
   the agent pasted that line into `exec` verbatim and the call was denied, because the binary
   it names is then `bash`. `apply.sh` reports such a directory on every deploy; it never
   deletes from the state dir, so move it aside by hand.

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

## Upgrade path: rehearse, and do not assume an intermediate hop is safer

The general rule in `SKILL.md` — cross a deep backlog in hops — was **wrong for this package**, and
the rehearsal is what showed it. Going through the last release of the previous line first (the
`extended-stable` tag) left an agent database at an older schema that the target release's
`doctor` then refused to inspect (*"uses schema version 1; run doctor --fix"* — from doctor
itself). The direct jump migrated cleanly in one pass. So:

1. **Rehearse on a copy before deciding.** `cp -a` the state dir, then run the target image
   against the copy: `docker run --rm -v <copy>:/home/node/.openclaw <image> node openclaw.mjs
   doctor --fix --non-interactive`. Exit 0 with `Errors: 0` is the bar. Then start the gateway
   against the copy in a throwaway container (dummy `OPENCLAW_GATEWAY_TOKEN`, no Slack tokens, no
   published ports) and poll `/startupz` and `/readyz` from inside it — startup runs migrations
   doctor does not, and this is the only way to see them without touching the live agent.

   **A copy taken while a 2026.9-line gateway is running carries its owner lease**, and the copy
   then refuses to start with *"Another Gateway owner lease is still active for this state
   directory"* — nothing to do with whatever you were rehearsing. The lease is a row in
   `state/openclaw.sqlite` (`state_leases`, `scope='gateway-owner'`, expiring five minutes after
   the last heartbeat, which the copy never receives) plus lock files under `tmp/openclaw-1000/`.
   In the **copy only**: `rm -rf <copy>/tmp` and delete that row, e.g.
   `python3 -c "import sqlite3;c=sqlite3.connect('<copy>/state/openclaw.sqlite');c.execute(\"delete from state_leases where scope='gateway-owner'\");c.commit()"`.
   Never touch the live state's lease.
2. **Fix what doctor will not.** Some renamed keys are validation errors rather than migrations:
   in the move to 2026.9, `channels.slack.nativeStreaming` had to go and `channels.slack.streaming`
   had to become `{ "mode": "partial", "nativeTransport": true }` by hand before doctor would run
   at all. Doctor's own error names the key.
3. **The rehearsal's output is the new tracked `openclaw.json`.** Doctor and startup rewrite the
   file (exec `security`/`ask` → `mode`, `meta.lastTouchedAt` and the cron store → SQLite,
   `memorySearch` → `memory.search`, a `plugins.entries` block, a `skills.entries` block disabling
   every bundled skill). Commit the migrated file **with** the tag bump; otherwise `apply.sh`
   copies the old shape back over it on the next deploy and the docs say plainly that doctor
   "does not update a stale deployment source". A plain restart does not rewrite the file, so
   tracked == live holds once that is done.
4. **Disable the bundled `codex` plugin** (`plugins.entries.codex.enabled: false`) before the first
   start on a new line. Left enabled it bootstraps `agents/main/agent/codex-home/` with its own
   SQLite stores and skills, and registers a cloud provider catalog — none of which this agent uses.
5. **Verify with the probes.** `/healthz`, `/startupz`, `/readyz` on 18789 answer without auth and
   already existed on the old line, so they are a before/after check.

Only hop if the direct rehearsal fails.

**Sandboxing is a reason to upgrade.** 2026.3.2 can only sandbox tool execution in **Docker**, which
this container cannot reach — it has no Docker socket and no Docker CLI, so `sandbox explain` reports
`runtime: direct`. Granting the host socket would be worse than the risk it mitigates: sandboxing
needs `container create`, and anything that can create a container with arbitrary bind mounts on the
host daemon is root. A socket proxy does not help, because proxies filter by endpoint rather than by
payload.

Current releases add **Podman, SSH, OpenShell and Crabbox** backends. Podman rootless would sidestep
the host-socket problem entirely. When that becomes available, the mode to want is
`sandbox.mode: "non-main"` — group and channel sessions count as non-main, so Slack messages (the
prompt-injection surface) get sandboxed while the main session stays on the host. Note also that
sandboxing never covers the gateway process itself, which is what holds the Slack and gateway
tokens, and that `tools.elevated` runs exec outside the sandbox — which is why it is disabled above.

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
