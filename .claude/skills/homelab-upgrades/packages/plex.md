# Plex (cinema)

```json
{ "name": "plex", "tier": "drift", "host": "cinema", "container": "plex",
  "compose": "cinema/docker-compose.yml",
  "image": "plexinc/pms-docker",
  "upstream": { "kind": "plex-json" },
  "tag_from_release": "identity" }
```

## Reading the versions

Plex publishes **no GitHub release feed**. Latest comes from the public download channel:

```bash
curl -fsS https://plex.tv/api/downloads/5.json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["computer"]["Linux"]["version"])'
```

That returns the same string format the `plexinc/pms-docker` tag uses (for example
`1.43.4.10903-e5521bd8c`), so it compares directly against the deployed tag. Note this is the
**public** channel; Plex Pass builds are ahead of it.

The running server also reports its own version without needing a shell on the host:

```bash
curl -fsS http://192.168.0.13:32400/identity      # only while cinema is awake
```

**cinema is asleep most of the time.** Both the identity endpoint and SSH will time out, and that
is normal. Report the last known version with its age rather than waking the machine to read a
version — there is no unattended alerting on cinema anywhere in this repo, deliberately.

## Hazard

Moderate, and social rather than technical: the library database at `/root/plex/config` is what
holds watch state, metadata and collections. People notice when it breaks.

Absolute paths are load-bearing here. The stack was originally started with `sudo`, so `~` resolved
to `/root` — the config, transcode and media mounts point at `/root/plex/...` and
`/mnt/storage/plex`. Do not "tidy" these to `/home/robby`; the existing library is where it is.

## Contracts

- `network_mode: host` — Plex needs it for DLNA and discovery. Nothing in this repo proxies it.
- `PLEX_CLAIM` is only used to claim a *new* server. It must stay empty for an existing one; a
  claim token on an established server can reassign it.
- `forge/openclaw/scripts/plex-power.py` wakes and sleeps cinema over SSH and checks liveness with a
  TCP connect to port 22 — not to Plex. A Plex upgrade cannot break the power scripts, but note the
  agent's key on cinema is restricted by forced command to `suspend` and `health` only, so **the
  agent cannot read Plex's version**; that has to come from the identity endpoint or a `robby` SSH
  session.

## Before upgrading

- Back up `/root/plex/config` — it is not in the Sunday backup set. `backup-to-tower.sh` covers
  bastion's data and `sync-forge.sh` covers forge's; cinema is not backed up by either.
- Cinema must be awake for the whole upgrade. Wake it first (`plex-power.py wake`, or
  `deploy.sh` will wake it) and confirm it is up before starting.
- Check whether the target build requires a newer client on the TVs or phones in use.

## Verify after

```bash
curl -fsS http://192.168.0.13:32400/identity
ssh robby@192.168.0.13 "docker logs --tail 40 plex"
```

Then play something, from a client that is not the machine you are working on. Confirm the library
is intact — counts, artwork, and watch state — rather than only that the server responds.

## Rollback

Restore the previous tag and redeploy. Plex database upgrades are generally forwards-only, so if the
library will not load, restore `/root/plex/config` from the backup taken above.
