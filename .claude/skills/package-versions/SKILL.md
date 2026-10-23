---
name: package-versions
description: Check whether a homelab package has a newer upstream release. Use when asked if Caddy, Vaultwarden, AdGuard Home, OpenClaw, Grafana, Prometheus, Omada, Plex, ollama, open-webui, Loki, Alloy, Postgres or an exporter has an update available. Planning or applying an upgrade is the homelab-upgrades skill instead.
---

# Is there a newer release?

Two lookups, and they are the only web calls the exec policy allows. Run the line exactly as
printed — no pipes, no `sed`, no `python3 -c`. Read the version out of the JSON yourself;
`| head -c 2000` is allowed when a body is long.

GitHub-released packages (`<owner>/<repo>` from the package file, see below):

```
curl -fsS https://api.github.com/repos/<owner>/<repo>/releases/latest
```

Plex, which publishes no release feed — the version is `computer.Linux.version`:

```
curl -fsS https://plex.tv/api/downloads/5.json
```

## Reading the answer

- `.claude/skills/homelab-upgrades/packages/<name>.md` carries a JSON block with that package's
  `upstream` repo and its `tag_from_release`: `identity` or `strip-v`. Caddy's release is `v2.11.4`
  and its docker tag drops the `v`; AdGuard keeps it in both. Getting this wrong reports a current
  package as behind.
- GitHub's release list contains non-version tags (`openclaw/openclaw` returns `linux-stable`
  among real releases), which is why this uses `releases/latest` and never list position.

## What you cannot see from here

The running version comes from `docker inspect`, and that is not on your allowlist — so you can
report what upstream publishes, not the gap. Say which half you have rather than implying the
package is behind or current. Plex lives on cinema, which is usually asleep; a missing Plex
version is normal.

**A check that could not run is not an all-clear.** If a lookup fails, say "unknown", never
"current".
