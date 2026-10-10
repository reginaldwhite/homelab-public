# UniFi Network application (and its MongoDB)

```json
{"name": "unifi", "tier": "watch", "host": "bastion", "container": "unifi",
 "compose": "bastion/wifi/docker-compose.yml", "image": "lscr.io/linuxserver/unifi-network-application",
 "upstream": {"kind": "github-release", "repo": "linuxserver/docker-unifi-network-application"},
 "tag_from_release": "identity",
 "companion": {"container": "unifi-db", "image": "mongo",
               "upstream": {"kind": "docker-hub", "repo": "library/mongo", "track": "same major"}}}
```

## Hazard

Holds the admin login and the configuration of the UniFi access points (the IoT SSID and its
password), LAN-only behind Caddy and on its own port. Its database is a separate `mongo`
container on a loopback publish, and **the two move together**: the application supports a
range of MongoDB majors (the linuxserver README's table is the authority at upgrade time),
MongoDB never upgrades across a major on its own, and a MongoDB major bump is a migration on
the `wifi_unifi_db` volume. Stay on the pinned MongoDB major until the application drops it.

The release tag is UniFi's version plus linuxserver's build number (`-lsNNN`); a new build
number alone is a base-image refresh, worth taking but not urgent.

The access point keeps serving WiFi with the controller down — only changes and the client
history stop.

## Before

- Last night's `~/backups/unifi-YYYY-MM-DD.tgz` exists (the application's own autobackup
  files; the UI restores them). Across a UniFi major, also take a backup from the UI by hand.
- Confirm the new UniFi version still lists the pinned MongoDB major as supported.
- For a MongoDB major bump: dump first
  (`docker exec unifi-db mongodump --archive -u root -p … --authenticationDatabase admin > ~/unifi-db-pre-<ver>.archive`),
  move one major at a time, and set `featureCompatibilityVersion` between hops as MongoDB's
  own upgrade notes require.

## Contracts

- `bastion/wifi/unifi/init-mongo.sh` runs only on an empty volume; the `MONGO_*` variables are
  read by the application on first run only and then live in its `system.properties`.
- The host ports in the compose file's header comment (8080 inform cannot change); AdGuard's
  `unifi` rewrite to 192.168.0.10; Caddy's `unifi.robbywhite.net` vhost.
- Phase 4 of `docs/plan-wifi.md`: `wifi-clients.py` reads the local API with a read-only user.

## Verify after

```bash
ssh robby@192.168.0.10 "docker logs unifi --since 3m 2>&1 | grep -iE 'error|exception|mongo' | head"
curl -sk -o /dev/null -w '%{http_code}\n' https://unifi.robbywhite.net/     # from the LAN: 200
```
Then sign in: every AP shows *connected* and the client list is populating.

## Rollback

Restore the previous tag and redeploy; the application tolerates going back within a major. If
it does not start, stop both containers, remove the `wifi_unifi_config` and `wifi_unifi_db`
volumes (by hand, not the agent), start the old tags empty and restore the latest
`~/backups/unifi-*.tgz` file through the wizard.
