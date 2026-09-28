# cAdvisor

```json
{"name": "cadvisor", "tier": "drift", "host": "bastion, forge, cinema", "container": "cadvisor",
 "compose": "bastion/observability/docker-compose.yml, forge/docker-compose.yaml, cinema/docker-compose.yml",
 "image": "ghcr.io/google/cadvisor",
 "upstream": {"kind": "github-release", "repo": "google/cadvisor"}, "tag_from_release": "identity"}
```

## Hazard

Per-container metrics on `:9200`, host network, on every host, with read-only mounts of `/`,
`/sys` and `/var/lib/docker`. Exposes container names and resource use, no secrets. **Pull
from `ghcr.io`, not `gcr.io`**: the gcr.io tags stopped at v0.55.1. Three hosts, one commit.

## Before

Nothing.

## Contracts

- The `container_*` metric names and the `name` label the *Containers* dashboard uses.
- `--docker_only=true` and `--port=9200` flags.

## Verify after

```bash
curl -s http://192.168.0.10:9200/metrics | grep -c '^container_cpu_usage_seconds_total'
```
Non-zero, then the *Containers* dashboard for each host.

## Rollback

Restore the tag on all three hosts and redeploy each.
