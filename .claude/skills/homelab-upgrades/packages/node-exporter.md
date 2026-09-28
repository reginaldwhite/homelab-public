# node exporter

```json
{"name": "node-exporter", "tier": "drift", "host": "bastion, forge, cinema", "container": "node-exporter",
 "compose": "bastion/observability/docker-compose.yml, forge/docker-compose.yaml, cinema/docker-compose.yml",
 "image": "prom/node-exporter",
 "upstream": {"kind": "github-release", "repo": "prometheus/node_exporter"}, "tag_from_release": "identity"}
```

## Hazard

Host metrics on `:9100`, host network, on every host. Exposes no secrets. Collector names and
metric names occasionally change between minors, which shows up as an empty panel rather than
an error. Three hosts, one commit.

## Before

Nothing. Skim the release notes for renamed collectors; the textfile collector flag is the one
this repo depends on.

## Contracts

- `--collector.textfile.directory`: bastion's scripts write `.prom` files there
  (`textfile-metrics.sh`, `adguard-stats.py`, `iot-poll.py`).
- The metric names the *Hosts* dashboard (node-exporter-full) queries.

## Verify after

```bash
curl -s http://192.168.0.10:9100/metrics | grep -E '^homelab_' | head
```
Then the *Hosts* dashboard for each host.

## Rollback

Restore the tag on all three hosts and redeploy each.
