# open-webui (forge)

```json
{ "name": "open-webui", "tier": "drift", "host": "forge", "container": "open-webui",
  "compose": "forge/docker-compose.yaml",
  "image": "ghcr.io/open-webui/open-webui",
  "upstream": { "kind": "github-release", "repo": "open-webui/open-webui" },
  "tag_from_release": "strip-v" }
```

## Hazard

Lowest of the seven. It is a web UI on the LAN; if it breaks you see it immediately, and nothing
else in the homelab depends on it. Its state is the `openwebui_data` volume — chat history and
settings, not irreplaceable.

Note it was pinned off the `main` branch tag, which moves whenever upstream pushes. Keep it pinned
to a release.

## Before upgrading

- This is a `0.x` project, so a **minor** bump is the breaking one. Expect schema migrations to
  `openwebui_data` between minors, and know they are usually one-way.
- Release notes here are large — some single releases exceed 100 KB. Read the upgrade or migration
  notes rather than the full changelog.
- If chat history matters, back up the `openwebui_data` volume first. It is **not** in the Sunday
  backup set as a named path; `sync-forge.sh` covers `openclaw-state/`, not docker volumes.

## Contracts

- `OLLAMA_BASE_URL=http://ollama:11434` over the `ai-network` compose network. A release that
  changes how the ollama connection is configured will present as "no models found".
- `depends_on: ollama (service_healthy)` — it waits on ollama's healthcheck, not on
  `model-loader`, so a failed model pull does not keep the UI down.
- `extra_hosts: host.docker.internal:host-gateway` is present for reaching host services.

## Verify after

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.0.12:8080
ssh robby@192.168.0.12 "docker logs --tail 40 open-webui"
```

Open the UI, confirm the model list is populated from ollama, and send one prompt. An empty model
list is the usual symptom of a changed connection setting.

## Rollback

Restore the previous tag and redeploy. **If a minor bump migrated `openwebui_data`, rolling back the
tag alone may not work** — the older version may refuse the migrated schema. Restore the volume
backup, or accept losing history.
