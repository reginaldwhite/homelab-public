# Loki

```json
{"name": "loki", "tier": "drift", "host": "bastion", "container": "loki",
 "compose": "bastion/observability/docker-compose.yml", "image": "grafana/loki",
 "upstream": {"kind": "github-release", "repo": "grafana/loki"}, "tag_from_release": "strip-v"}
```

## Hazard

The log store. Never published; on the stack's own network plus the vaultwarden network for
Caddy's `loki.robbywhite.net` ingest vhost. Holds thirty days of every host's journal and
container output and AdGuard's query log — history, not state; losing it costs nothing that
matters. Schema and storage formats move between minors (tsdb versions, the single-binary
target's defaults), and the config is validated at start, so a bad bump fails loudly.

## Before

Nothing to back up. Read the release's *upgrade* notes for `schema_config` and
`limits_config` renames; `bastion/observability/loki/loki.yml` is small enough to compare against
the reference config in the release.

## Contracts

- `loki.yml` keys: `schema_config` (tsdb v13 today), `limits_config.retention_period`,
  `compactor.retention_enabled`.
- The push API Alloy uses (`/loki/api/v1/push`) and the query API Grafana's data source uses.
- `apply.sh --validate` runs `loki -verify-config` against the file.

## Verify after

```bash
ssh robby@192.168.0.10 "docker exec grafana wget -qO- http://loki:3100/ready; docker exec grafana wget -qO- 'http://loki:3100/loki/api/v1/labels' | head -c 300"
```
Then open *Logs* in Grafana and confirm lines from all three hosts within the last few minutes.

## Rollback

Restore the previous tag and redeploy. If the new version migrated the index format, the old
binary may refuse the volume; the data is regenerable, so removing `observability_loki_data`
is an acceptable last resort.
