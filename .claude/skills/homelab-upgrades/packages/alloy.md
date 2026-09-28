# Alloy

```json
{"name": "alloy", "tier": "drift", "host": "bastion, forge, cinema", "container": "alloy",
 "compose": "bastion/observability/docker-compose.yml, forge/docker-compose.yaml, cinema/docker-compose.yml",
 "image": "grafana/alloy",
 "upstream": {"kind": "github-release", "repo": "grafana/alloy"}, "tag_from_release": "identity"}
```

## Hazard

The log shipper, one instance per host, all running the identical `shared/alloy/config.alloy`.
Runs as root to read journald; talks to Docker only through the read-only socket proxy. Holds
the Loki credential from the host's `.env`. Three hosts means three tag lines to bump — do them
in one commit, since the config is shared and a component renamed in a new version breaks every
host at once.

## Before

Nothing to back up. Read the release notes for renamed or removed components
(`loki.source.journal`, `discovery.docker`, `loki.process` stages) — Alloy deprecates with a
warning for a few minors, then removes.

## Contracts

- `config.alloy` as a whole; `apply.sh --validate` runs `alloy validate` on it before deploy.
- The `LOKI_URL`, `LOKI_USER`, `LOKI_PASSWORD`, `ALLOY_HOST` environment variables.
- The socket proxy's allow-list (`CONTAINERS=1`, `NETWORKS=1`); a new discovery feature that
  needs another endpoint will 403 until the proxy is told.

## Verify after

```bash
ssh robby@192.168.0.10 "docker logs alloy --since 2m 2>&1 | grep -iE 'level=(error|warn)' | head"
```
Then *Logs* in Grafana: every host still shipping (a `host` label with recent lines for each).

## Rollback

Restore the previous tag on all three hosts and redeploy each.
