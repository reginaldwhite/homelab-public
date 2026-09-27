# AdGuard Home (bastion)

```json
{ "name": "adguard", "tier": "watch", "host": "bastion", "container": "adguardhome",
  "compose": "bastion/vaultwarden/docker-compose.yml",
  "image": "adguard/adguardhome",
  "upstream": { "kind": "github-release", "repo": "AdguardTeam/AdGuardHome" },
  "tag_from_release": "identity" }
```

## Hazard

AdGuard is **DNS and DHCP for the whole house**. A bad upgrade does not just break this container —
it takes out name resolution for every device, including the path you would use to fix it, and
eventually leases as well.

It runs `network_mode: host`, so it binds port 53 directly on bastion. There is no container
network indirection to hide behind.

## Before upgrading

- **Have a second resolver configured on the machine you are working from**, or a plain IP route to
  bastion. Do not start this with only DNS-dependent access.
- Config and state live at `/home/robby/vaultwarden/agh-conf` and `agh-work`. They **are** backed up
  to vault in the Sunday window, but not where you would look for them: `backup-to-tower.sh` stages
  the whole of `/home/robby/vaultwarden/` — which is the parent of `vw-data`, `agh-work` and
  `agh-conf` — so AdGuard's config lands under **`/mnt/user/backups/vaultwarden/agh-conf/`**. There is
  no `backups/adguard/` directory. (The staging step runs through a root container on purpose:
  `agh-work` and `agh-conf` are root-owned 0700/0600, so robby's rsync cannot read them directly.)
- **That backup is a mirror, not a history.** The weekly dated snapshots in
  `backups/vaultwarden-snapshots/` tar `vw-data` only — AdGuard's config has no dated copies, and the
  mirror runs with `--delete`, so a corrupted `AdGuardHome.yaml` reaching vault leaves nothing older
  to fall back to. **Take your own copy of `agh-conf/` before upgrading.** This is the one asymmetry
  with Vaultwarden on the same host.
- Read `bastion/adguard-dhcp-runbook.md` before touching anything DHCP-related.
- Prefer a patch hop within the same minor. AdGuard is a `0.x` project, so a **minor** bump
  (`0.107` → `0.108`) is the breaking one, not the third component.

## Contracts

- Nothing in this repo parses AdGuard's output.
- The admin UI is deliberately **not** exposed to the internet. It was, until 2026-09-26. If an
  upgrade changes how the UI binds, do not "fix" it by publishing the port.

## What an upgrade does to the config

- **`config_migrator` bumps `schema_version` in `AdGuardHome.yaml` on first start** (33 → 34 on
  the last patch-level hop) and the migration is one-way, which is why the copy above is taken
  before, not after. The log line is `config_migrator: upgrade yaml from=N to=M`.
- **Expect one `dnsproxy: exchange failed upstream=…` error in the first ten seconds after
  start** — the DoH upstream is not ready before the first query arrives. It is not a regression
  unless it repeats: count them with `docker logs adguardhome 2>&1 | grep -c 'exchange failed'`
  and verify with a *second* query, not the first.
- The DHCP check below only applies if `dhcp.enabled` is `true` in the YAML. It was `false` at the
  last upgrade — the router still serves leases — so resolution is the whole verification.

## Verify after

```bash
dig @192.168.0.10 example.com +short          # from another machine, not bastion
dig @192.168.0.10 example.com +short          # run twice: second should be cached and faster
ssh robby@192.168.0.10 "docker logs --tail 40 adguardhome"
```

Then confirm DHCP is still leasing — a device rejoining the network should get an address. See the
DHCP runbook for how to check leases without the UI.

## Rollback

Restore the previous tag and redeploy. `agh-conf` is backwards compatible within a `0.107.x` line;
across a minor bump it may have been rewritten, in which case restore `AdGuardHome.yaml` from the
copy you took before starting — or from `/mnt/user/backups/vaultwarden/agh-conf/` on vault, which is
last Sunday's state and nothing older.
