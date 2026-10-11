# Omada SDN controller

```json
{"name": "omada", "tier": "watch", "host": "bastion", "container": "omada",
 "compose": "bastion/wifi/docker-compose.yml", "image": "mbentley/omada-controller",
 "upstream": {"kind": "docker-hub", "repo": "mbentley/omada-controller",
              "track": "highest N.N.N.N tag with no suffix; never `latest`, never `beta-*`"},
 "tag_from_release": "identity"}
```

## Hazard

Holds the admin login and the full configuration of the EAP772 (SSIDs and their passwords,
schedules, radio settings), LAN-only behind Caddy and on its own port. The image bundles its
own MongoDB; a controller version bump can carry a database upgrade with it, which runs
forward only. **`latest` on this image still points at v5** by the maintainer's choice, so
the tag is always written out in full. A `beta-*` tag is TP-Link's beta channel; not here.

The access point keeps serving WiFi with the controller down — only changes, the client
history and the scheduler stop. Nothing else in the homelab depends on it.

## Before

- Last night's `~/backups/omada-YYYY-MM-DD.tgz` exists (the controller's own autobackup
  files; the UI imports them). Across a major version (`N.` changes), also export a backup
  from the UI by hand first and read mbentley's upgrade guide in the README — v5→v6 changed
  the database engine and needed a migration.
- Check the release notes for a **minimum AP firmware**; the EAP772 is upgraded from the
  controller, and a controller too new for the AP's firmware drops it to *disconnected*.

## Contracts

- The host ports in the compose file's header comment; AdGuard's `omada` rewrite to
  192.168.0.10; Caddy's `omada.robbywhite.net` vhost.
- Phase 4 of `docs/plan-wifi.md`: `wifi-clients.py` reads the OpenAPI with a client credential
  created in the UI. The API's auth flow has changed between minors before.
- `omada-ssid.py` writes SSIDs, time-range profiles and MAC groups through
  `/wireless-network/wlans/{wlanId}/ssids/{ssidId}/update-*`, `/time-range-profiles` and
  `/profiles/groups`; the request schemas are in the controller's own `/v3/api-docs` (a copy
  sits in `~/omada-api/` on bastion). After an upgrade, `omada-ssid.py list` and a `--dry-run`
  write are the contract check.

## Verify after

```bash
ssh robby@192.168.0.10 "docker logs omada --since 3m 2>&1 | grep -iE 'error|exception' | head"
curl -sk -o /dev/null -w '%{http_code}\n' https://omada.robbywhite.net/     # from the LAN: 200
```
Then sign in: the EAP772 shows *connected*, the client list is populating, and the
scheduler profiles are still attached to their SSIDs.

## Rollback

Restore the previous tag and redeploy. If the controller refuses the data directory after a
downgrade, stop it, remove the `wifi_omada_data` volume (by hand, not the agent), start the
old tag empty and import the latest `~/backups/omada-*.tgz` file through the wizard.
