# Deployment model: finishing the job on forge and cinema

`bastion` is migrated to `deploy.sh`. `forge` and `cinema` are not, and forge is where the
damage has actually happened. This documents the failure, compares the two models against it,
and specifies the migration.

## The incident

**A process read its configuration from a git working tree that a human was editing.** On forge,
the OpenClaw container bind-mounts `forge/openclaw-state/` and the stack is brought up from
`forge/docker-compose.yaml` — both inside the clone used for day-to-day work. Every `git switch`
silently rewrites live production config.

It happened four times in one evening:

| # | What happened | How it surfaced |
|---|---|---|
| 1 | Checked out `main` while the context fix was on an unmerged branch. `openclaw.json` reverted to `contextWindow: 4096`, and `OLLAMA_CONTEXT_LENGTH` vanished from the compose file. | Nothing. The running container still held the good values in memory. |
| 2 | Same revert, still unnoticed, until OpenClaw re-read config and the agent began failing with `Model context window too small (4096 tokens)`. | Reported by the user — *after* the fix had supposedly been applied. |
| 3 | A later `git switch` aborted: the running container had touched `models.json`, so git saw a possibly-dirty tracked file. | Blocked a checkout mid-task. Content was identical; a `status` refresh cleared it. |
| 4 | The `@reboot` cron job pointed at `forge/scripts/vaultwarden-version-check.sh` *inside the tree*. A branch switch would have deleted the file. | Caught before it fired. Fixed by copying to `~/scripts/`. |

Two properties made this nasty:

- **The blast radius is invisible at the moment of damage.** Containers hold config in memory, so
  a revert does nothing until the next restart — which may be hours later, or at 5am when forge
  wakes. Cause and symptom are separated in time, which is why #1 and #2 are the same bug.
- **It is silent in both directions.** Git rewrites the app's config; the app rewrites git's
  files. Neither side notices, and `git status` churn trains you to ignore it.

The same family of bug hit the Caddyfile independently: a single-file bind mount pinned the
inode, git replaced the file, and the container kept serving stale config. Already fixed by
mounting the directory.

## The two models

Framing this as "push vs pull" misses the cause. **The incident was not caused by how config
reaches the host — it was caused by the runtime reading from a branchable, human-edited tree.**
Either model can fix it, and either model can reproduce it. What follows compares them anyway,
because the secondary differences do matter.

### A. Central push to a deployed copy — today's `deploy.sh`

rsync `<host>/` → staging on the host → `apply.sh --validate` → show the diff → rsync to the
live copy → `apply.sh`.

**For**

- Hosts hold no git at all: no clone, no branch, no repo credentials. The deployed copy is inert
  — there is no command that switches it to something else. This eliminates the incident by
  construction rather than by discipline.
- **Validation happens before the live copy changes.** Staging is validated first, so a broken
  compose file or Caddyfile never reaches the running config. A pull model mutates the working
  tree first and validates after, leaving a window where the tree is broken.
- `--check` shows exactly which files would change, per host, before anything moves.
- One place to look for "what is deployed where", and one place to add a host.
- Secrets stay put: `.env` is excluded from rsync and survives deploys.

**Against**

- Requires reaching the host. cinema is asleep most of the time and forge is down 11pm–5am, so
  `deploy.sh all` fails for them today — addressed by waking them, below.
- The repo has to live somewhere, and today that is forge — so forge deploys are a local rsync,
  but a *bastion-initiated* deploy would need a clone on bastion.
- No automatic convergence: a host that missed a deploy stays on its last deployed copy until
  someone redeploys. (It does not *drift*, it is simply behind.)

### B. Per-host clone that pulls

Each host keeps a clone; deploying means `git pull --ff-only` on the host and running apply.

**For**

- A host can converge on its own — at boot, or on a timer — with no control node. That suits
  machines that sleep: forge could fix itself at 5am without anyone initiating.
- No rsync or SSH fan-out from a control node.

**Against**

- **Every host gains a branchable working tree.** That is the incident, reintroduced. It can be
  mitigated with guards (refuse to deploy from non-`main`, refuse when dirty — the old
  `bastion/deploy.sh` did exactly this), but the hazard is structural: the thing production reads
  from is a thing a person can `git switch`.
- Each host needs read credentials for the repo — more secret sprawl, on more machines.
- Validation comes after the tree has already changed.

### Decision

**Stay with A and finish migrating forge, then cinema.** The deciding factor is that a deployed
copy has no branch to switch. Model B needs discipline enforced by guards to avoid an incident
that model A cannot have. The staging-and-validate ordering is a real second win.

The sleeping-host gap is model A's one real weakness, and it is better closed than accepted:
deploy should wake the host it is deploying to (next section). That removes the main argument for
model B without putting a branchable tree back on the network.

## Waking hosts on deploy

`deploy.sh` currently gives up on an unreachable host (`"$host is unreachable (asleep?)"`). Since
forge and cinema are asleep by design for much of the day, it should wake them instead.

Add a MAC per host and, when SSH fails, send a Wake-on-LAN magic packet and poll for SSH:

| Host | MAC | Wakeable |
|---|---|---|
| `forge` | `88:d7:f6:13:14:3a` | yes |
| `cinema` | `d0:17:c2:dd:c1:c2` | yes |
| `bastion` | — | always on |
| `vault` | — | **no** — no WoL support; powered by a smart plug on a fixed schedule |

Reuse the approach already in [bastion/scripts/wake-forge.sh](../bastion/scripts/wake-forge.sh):
try `wakeonlan`/`etherwake`, fall back to a short Python broadcast, so nothing extra needs
installing. The magic packet is layer 2, so this only works from a host on the same LAN — true
for every machine here.

Details that matter:

- **Poll, don't sleep a fixed amount.** Retry SSH every 5s for ~180s, then fail with a clear
  message. Boot time varies.
- **Wait for Docker too.** A just-woken host will accept SSH before `dockerd` is ready, so
  `apply.sh` should retry `docker info` briefly rather than failing on a race. This is the same
  boot-race the version-check script already handles.
- **Leave the host awake, and say so.** Sleeping it again would be surprising and risky — someone
  may be using it. Print an explicit note instead: *"cinema was asleep; woken for deploy and left
  running — sleep it with `plex-power.py sleep`"*. forge will shut itself down at 11pm via
  bastion's cron regardless.
- **`--no-wake` to opt out**, for when you do not want a 3am deploy powering on the living room
  Plex box.

This turns `deploy.sh all` from "fails for anything asleep" into "converges everything", which is
the property model B was attractive for.

## Migrating forge

The tricky part is `openclaw-state/`, which is three things at once: config we edit, state the
container writes, and state we must never lose.

### The problem to avoid

A naive migration — `rsync -a --delete forge/ → $DEST/forge/` with the compose file still
mounting `./openclaw-state` — would **destroy the agent**. These live only on disk, are gitignored,
and would be deleted on the first deploy:

```
agents/main/sessions/   canvas/   cron/   devices/   identity/   memory/   workspace/
update-check.json
```

`workspace/` contains its own git repo, and `memory/` is the agent's accumulated memory.
Separately, these are **tracked in git but written by the container at runtime**, so a deploy
would revert them to whatever git last saw:

```
credentials/slack-pairing.json   credentials/slack-default-allowFrom.json
exec-approvals.json              logs/config-audit.jsonl
```

### The split

Mirror what bastion already does with `vw-data`: live data lives outside both the repo and the
deployed copy, referenced by absolute path.

| Path | Role | Deployed? |
|---|---|---|
| `/home/robby/openclaw-state/` | all live agent state | **no** — never touched by deploy |
| `forge/openclaw/openclaw.json` | gateway config | yes, copied into live state |
| `forge/openclaw/models.json` | agent model definitions | yes, copied into live state |
| `forge/openclaw/scripts/*.py` | power-control tools | yes, copied into live state |

Everything else under `openclaw-state` stops being tracked. `credentials/`, `exec-approvals.json`
and `logs/` are runtime state that happened to get committed early; they contain no secrets
(verified: the slack files hold only `allowFrom`/`requests`/`version`), so untracking them needs
no history rewrite. `openclaw.json.bak` goes too — it is a backup artifact.

**Deploy copies files in; it never deletes from the live state directory.** That is the whole
safety property, and it is why the config subset is copied file-by-file rather than rsync'd with
`--delete`.

### Steps

1. **Move the live state**, once, by hand:
   `docker compose stop openclaw && mv forge/openclaw-state /home/robby/openclaw-state`
2. **Restructure the repo**: `forge/openclaw/{openclaw.json,models.json,scripts/}` as the tracked
   config subset; `git rm --cached` the runtime files listed above; delete the now-stale
   `forge/.gitignore` entries.
3. **Absolute-path the mount** in `forge/docker-compose.yaml`:
   `/home/robby/openclaw-state:/home/node/.openclaw`. A relative path would resolve against the
   deployed copy and create an empty state directory.
4. **Write `forge/apply.sh`** following bastion's contract:
   - `--validate`: `docker compose config --quiet`, `python3 -c json.load` on both JSON files,
     `bash -n` on the scripts, and a crontab diff.
   - apply: copy the config subset into `/home/robby/openclaw-state/`, install `forge/crontab`,
     `docker compose up -d`.
   - **Restart `openclaw` when `openclaw.json` or `models.json` changed.** OpenClaw caches config
     at startup — proven tonight, when the agent kept failing until the container was restarted
     after the file was already correct. Without this, a deploy would appear to succeed and
     change nothing.
5. **Move forge's cron to the deployed copy.** `forge/crontab` with
   `@reboot /home/robby/homelab-deployed/forge/scripts/vaultwarden-version-check.sh`, matching
   how bastion's cron runs from its deployed copy. Drop the hand-copy to `~/scripts/`.
6. **Add `forge` to `MIGRATED`** in `deploy.sh`.
7. **Fix the backup gap.** `sync-forge.sh` currently pushes only `/home/robby/Documents/`, which
   is where openclaw-state lives today. Moving it to `/home/robby/openclaw-state/` would silently
   drop the agent's memory, sessions and credentials from the weekly backup to vault. Add that
   path — this is the one step whose omission would be invisible until a restore was needed.

### Verification

```bash
./deploy.sh --check forge        # expect a clean diff, no deletions under openclaw-state
./deploy.sh forge
docker exec openclaw node openclaw.mjs status | grep -i sessions   # 16k ctx, sessions intact
docker exec openclaw python3 /home/node/.openclaw/scripts/forge-power.py status
docker exec openclaw python3 /home/node/.openclaw/scripts/plex-power.py status
ls /home/robby/openclaw-state/memory /home/robby/openclaw-state/workspace   # still there
```

Then the test that actually proves the incident is dead:

```bash
git switch -c scratch && git switch main     # previously reverted live config
grep -m1 contextWindow /home/robby/openclaw-state/openclaw.json   # expect 16384, unchanged
```

### Rollback

The live state never moves again after step 1, so rollback is: point the compose file back at the
old path, or `git revert` the branch and redeploy. Nothing in the deployed copy holds unique data.

## Migrating cinema

Lower value, and it has a wrinkle: the Plex stack was first started with `sudo`, so its bind
mounts live under `/root/plex/` and `apply.sh` would need root on cinema — either passwordless
sudo for `docker compose`, or moving the config out of `/root`. cinema is also asleep most of the
time, so deploys must be timed with a wake.

Recommend deferring until after forge is proven, then deciding whether it earns the sudo grant.

## Running deploys from OpenClaw

Short answer: **not as it stands, and the interesting question is whether it should.**

The container is missing the machinery:

| Needed | In the container? |
|---|---|
| `git`, `ssh`, `python3`, `curl` | yes |
| `rsync` | **no** — it is the core of how deploy copies files |
| `docker` CLI / socket | **no** — `apply.sh` runs `docker compose` |
| the repo itself | **no** — only `/home/node/.openclaw` is mounted, no host filesystem |

Mounting the repo and installing rsync would still leave Docker, and solving that means mounting
`/var/run/docker.sock` — host-root-equivalent access for a process running an LLM with web
tools. That is the same trade rejected when sandboxing came up, and it should be rejected here
for the same reason.

### If you want it anyway, do it as a trigger

Have the agent SSH to a host that already has the repo and run a **fixed** command, restricted in
`authorized_keys` with a forced command so the key cannot run anything else:

```
command="/home/robby/bin/deploy-trigger",restrict ssh-ed25519 AAAA... openclaw
```

The containment property is real: `deploy.sh` refuses anything but a clean checkout of
`origin/main`, so a triggered deploy can only ever apply **merged** config. The agent gains
"force convergence to main", not "change the homelab".

The honest caveat: that holds only as long as pushing to `main` is harder than triggering a
deploy. The GitHub PAT sits in `.secrets.env` on forge, so anything with a shell on forge can
push and then deploy. Treat the trigger as convenience, not as a security boundary.

### Related finding: the agent's key is not low-privilege

Narrowing the SSH mount removed `id_ed25519` (root on vault) from the container, but
`openclaw_ed25519` is itself authorized as `robby` on **both forge and bastion**, and `robby` is
in bastion's `docker` group — so SSH there is container control, which is root-equivalent.

Worth fixing independently of any deploy trigger: give that key forced commands and `restrict` in
each host's `authorized_keys`, limited to the power scripts it actually needs. That is the same
mechanism the trigger would use, so both can land together.

## Closing the class of bug

After forge migrates, every remaining "runtime reads from the git tree" instance is gone. To keep
it that way, add `deploy.sh --audit`: check each host's crontab and compose files for paths under
a git clone, and fail loudly. That turns a silent, delayed-onset failure into an immediate one —
which is the only reason it took four occurrences to see it tonight.
