# Vaultwarden (bastion)

```json
{ "name": "vaultwarden", "tier": "watch", "host": "bastion", "container": "vaultwarden",
  "compose": "bastion/vaultwarden/docker-compose.yml",
  "image": "vaultwarden/server",
  "upstream": { "kind": "github-release", "repo": "dani-garcia/vaultwarden" },
  "tag_from_release": "identity" }
```

## Hazard

This holds every password. Two separate reasons it is Watch tier:

- **Migrations are one-way.** A newer Vaultwarden upgrades its database schema on first start and
  the older binary cannot read the result. Rollback means restoring the data directory, not just
  the tag.
- **Being too old breaks clients silently.** Bitwarden clients refuse to sync against a server
  below their minimum and report it as an **opaque decoding error**, not a version warning. So
  staleness is discovered from a phone that will not sync rather than from anything that looks like
  an alert. This is the one package where an unattended check already exists
  (`forge/scripts/vaultwarden-version-check.sh`) precisely because of this.

## Before upgrading

- `apply.sh` tars `/home/robby/vaultwarden/vw-data` into `/home/robby/vw-backups/` immediately
  before starting a new version, and those tarballs are never pruned. **Confirm the tarball was
  actually written** before trusting the upgrade; do not assume it ran.
- `vw-data` also goes to vault in the Sunday window via `backup-to-tower.sh`, in **three** layers —
  worth knowing which one to reach for:

  | On vault | What it is | Rotation |
  |---|---|---|
  | `backups/vaultwarden/` | last Sunday's mirror | `--delete`, so no history |
  | `backups/vaultwarden-snapshots/` | dated weekly tarballs of `vw-data` | rotated, a few kept |
  | `backups/vaultwarden-pre-upgrade/` | `apply.sh`'s pre-upgrade tarballs | never pruned |

  For an upgrade rollback, `vaultwarden-pre-upgrade/` is the one you want. `vault-report.py` reports
  what actually landed, so check its age rather than assuming.
- Note that the Sunday backup **stops the container** for the local copy — `vw-data` is a live sqlite
  database and rsyncing it open can capture a torn write. It restarts immediately, but do not
  schedule an upgrade to overlap the 2:30am Sunday window.
- Read the release notes for a required minimum client version. Upgrading the server can strand an
  old client just as being too old strands a new one.

## Contracts

- `forge/scripts/vaultwarden-version-check.sh` compares the deployed tag to `tag_name` from the
  GitHub API **with no normalisation**, because Vaultwarden's release tags and docker tags are
  identical (`1.37.3`, no `v`). If upstream ever adds a `v` prefix, that comparison silently reports
  the server as permanently behind.

## Verify after

```bash
ssh robby@192.168.0.10 "docker logs vaultwarden | grep -i version | tail -5"
ssh robby@192.168.0.10 "curl -s -o /dev/null -w '%{http_code}' localhost:8081"
```

Then **sync a real client** — a phone or browser extension. The server starting proves nothing
about client compatibility, which is the failure mode that matters here.

## Rollback

Restore the previous tag *and* untar the pre-upgrade `vw-data` backup from
`/home/robby/vw-backups/` on bastion (mirrored to `backups/vaultwarden-pre-upgrade/` on vault).
Restoring the tag alone leaves a migrated database the older binary cannot read.
