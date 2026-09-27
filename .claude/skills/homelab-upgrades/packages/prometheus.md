# Prometheus

```json
{"name": "prometheus", "tier": "watch", "host": "bastion", "container": "prometheus",
 "compose": "bastion/observability/docker-compose.yml", "image": "prom/prometheus",
 "upstream": {"kind": "github-release", "repo": "prometheus/prometheus"}, "tag_from_release": "identity"}
```

## Hazard

Ninety days of metrics in the `observability_prometheus_data` volume. The TSDB format is stable
within a major; a major bump can change config syntax (`promtool check config` catches it, and
`apply.sh --validate` runs that against the new image before anything applies). No secrets.

## Before

Nothing. Data is regenerable history and is not backed up by design.

## Contracts

- `prometheus.yml` syntax, checked by `apply.sh --validate` with the new image's `promtool`.
- The HTTP API (`/api/v1/query`, `/api/v1/targets`) that Grafana and, from phase 4, the digester read.
- Retention flags in the compose `command:`; a renamed flag stops the container at start.

## Verify after

```bash
ssh robby@192.168.0.10 "docker exec prometheus wget -qO- http://localhost:9090/api/v1/targets" \
  | python3 -c 'import sys,json;t=json.load(sys.stdin)["data"]["activeTargets"];print(sum(1 for x in t if x["health"]=="up"),"of",len(t),"up")'
```
Expect every target up (forge and cinema excepted when asleep), then a dashboard with data.

## Rollback

Restore the previous tag and redeploy. Within a major the volume reads as it was.
