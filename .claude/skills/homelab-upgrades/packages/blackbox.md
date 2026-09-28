# blackbox exporter

```json
{"name": "blackbox", "tier": "drift", "host": "bastion", "container": "blackbox",
 "compose": "bastion/observability/docker-compose.yml", "image": "prom/blackbox-exporter",
 "upstream": {"kind": "github-release", "repo": "prometheus/blackbox_exporter"}, "tag_from_release": "identity"}
```

## Hazard

Probes the vault, AdGuard's rewrite and every host by ICMP. Stateless; the only thing that can
break is the module config. Its failure is self-announcing: the *Edge* dashboard goes blank.

## Before

Nothing. `apply.sh --validate` runs `--config.check` on `blackbox/blackbox.yml`.

## Contracts

- `blackbox.yml` module keys (`http_2xx`, `dns_vault_rewrite`, `icmp`).
- The `probe_*` metric names Prometheus and the Edge dashboard use.

## Verify after

```bash
ssh robby@192.168.0.10 "docker exec prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=probe_success' | head -c 600"
```
All targets `1`.

## Rollback

Restore the tag and redeploy.
