# Caddy (bastion)

```json
{ "name": "caddy", "tier": "watch", "host": "bastion", "container": "caddy",
  "compose": "bastion/vaultwarden/docker-compose.yml",
  "image": "caddy",
  "upstream": { "kind": "github-release", "repo": "caddyserver/caddy" },
  "tag_from_release": "strip-v" }
```

## Hazard

Caddy terminates TLS for `vault.robbywhite.net`, which is internet-facing. It is the most
exposed thing in the homelab and the reason this package is Watch tier despite moving slowly.

If Caddy is down, Vaultwarden is unreachable from outside even though it is running fine. On
bastion, `curl localhost:8080` is what separates the two: it hits Vaultwarden directly on the
loopback publish, so a success there with a failing HTTPS request means Caddy, not Vaultwarden.

## Before upgrading

- The TLS certificates live in the `caddy_data` and `caddy_config` volumes, named
  `vaultwarden_caddy_data` and `vaultwarden_caddy_config` because the compose project name is
  pinned to `vaultwarden`. **Do not rename the project or run compose from a different directory
  without the pinned name** — the volumes detach and Caddy re-requests certificates, which can hit
  Let's Encrypt rate limits.
- `bastion/vaultwarden/caddy/` is mounted as a **directory**, not a single file. That is
  deliberate: a single-file bind mount pins the inode, and git replaces files rather than editing
  in place, so after a pull the container kept serving the old Caddyfile while `caddy reload`
  silently reloaded stale config. Keep the directory mount.

## Contracts

- Nothing in this repo parses Caddy's output, so a version bump has no downstream callers.
- `apply.sh --validate` starts a throwaway container to check the Caddyfile. A Caddy release that
  changes Caddyfile syntax will therefore fail validation *before* it reaches the live copy, which
  is the intended behaviour — read the error rather than forcing past it.

## Verify after

```bash
curl -sI https://vault.robbywhite.net | head -1          # from off-LAN if possible
ssh robby@192.168.0.10 "curl -s -o /dev/null -w '%{http_code}' localhost:8080"
ssh robby@192.168.0.10 "docker logs --tail 40 caddy"
```

Check the certificate is the real one and not a self-signed fallback:

```bash
echo | openssl s_client -connect vault.robbywhite.net:443 2>/dev/null \
  | openssl x509 -noout -issuer -dates
```

## Rollback

Restore the previous tag in the compose file and redeploy. The `caddy_data` volume is untouched by
a tag change, so certificates survive a rollback — nothing needs re-issuing.
