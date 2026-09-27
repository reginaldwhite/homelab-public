# Grafana

```json
{"name": "grafana", "tier": "watch", "host": "bastion", "container": "grafana",
 "compose": "bastion/observability/docker-compose.yml", "image": "grafana/grafana",
 "upstream": {"kind": "github-release", "repo": "grafana/grafana"}, "tag_from_release": "strip-v"}
```

## Hazard

A login on the vault host, LAN-only behind Caddy. Its database is a SQLite file in the
`observability_grafana_data` volume holding users and preferences; dashboards, data sources and
alert rules are provisioned from git and reappear on any fresh volume. Grafana runs schema
migrations on start and does not run them backwards, so a downgrade needs the volume restored.

## Before

Nothing to back up for a same-major bump beyond the weekly copy of the volume. Across a major
version, copy the volume first:
`docker run --rm -v observability_grafana_data:/d:ro -v $HOME:/out alpine tar czf /out/grafana-data.pre-<ver>.tgz -C /d .`
Read the release's breaking-changes section; provisioning file formats have changed between majors.

## Contracts

- `provisioning/datasources/*.yml` and `provisioning/dashboards/*.yml` schemas (`apiVersion: 1`).
- Dashboard JSON schema version; community dashboards in `grafana/dashboards/` may need
  re-download after a major bump.
- The `GF_*` environment variables in the compose file; a renamed setting logs a warning at start.
- Phase 3+: the alerting API the digester reads (`/api/alertmanager/grafana/api/v2/alerts`).

## Verify after

```bash
ssh robby@192.168.0.10 "docker logs grafana --since 2m 2>&1 | grep -iE 'level=(error|warn)' | grep -v deprecat | head"
curl -sk https://grafana.robbywhite.net/api/health          # from the LAN: {"database":"ok",...}
```
Then sign in, open Hosts, Containers and Edge, and confirm each renders with data.

## Rollback

Restore the previous tag and redeploy; if the schema moved, restore the volume from the tarball.
