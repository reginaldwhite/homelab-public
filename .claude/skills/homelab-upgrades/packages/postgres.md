# Postgres (events-db)

```json
{"name": "postgres", "tier": "drift", "host": "bastion", "container": "events-db",
 "compose": "bastion/observability/docker-compose.yml", "image": "postgres",
 "upstream": {"kind": "docker-hub", "repo": "library/postgres", "track": "same major, -alpine"},
 "tag_from_release": "identity"}
```

## Hazard

The events store: one small database, never published, reachable only on the stack's own
network with a password from `.env`. Minor bumps within a major (17.x) are drop-in. **A major
bump is a migration**: the data directory format changes and the new image refuses to start on
the old volume. Stay on the current major until there is a reason to move.

## Before

The nightly dump in `~/backups/events-YYYY-MM-DD.sql.gz` is the backup; check it is from last
night. For a major bump, take a fresh one by hand:
`docker exec events-db pg_dump -U homelab -d homelab | gzip > ~/events-pre-<ver>.sql.gz`.

## Contracts

- `bastion/observability/postgres/001-events.sql` runs only on an empty volume.
- `scripts/event.sh` and `scripts/adguard-stats.py` write through `docker exec events-db psql`.
- Grafana's `Events` data source (provisioned, `grafana-postgresql-datasource`).

## Verify after

```bash
ssh robby@192.168.0.10 "docker exec events-db psql -U homelab -d homelab -c 'select count(*), max(ts) from events'"
```
Then *Jobs* and *Sauna and Sprinklers* in Grafana still render.

## Rollback

Minor: restore the tag and redeploy. Major: restore the tag, remove the new volume, recreate
and restore from the dump.
