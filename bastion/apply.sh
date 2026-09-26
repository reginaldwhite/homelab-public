#!/bin/bash
# apply.sh — Apply bastion's config. Run by ../deploy.sh on bastion, from the
# deployed copy (/home/robby/homelab-deployed/bastion), never from a git clone.
#
#   apply.sh --validate   # check this copy; changes nothing (run from staging)
#   apply.sh              # install crontab, compose up, reload caddy
#
# Cron runs the scripts straight from the deployed copy (see crontab), so there
# is nothing to copy for them.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$HERE/vaultwarden"
VW_DATA="/home/robby/vaultwarden/vw-data"
VW_BACKUPS="/home/robby/vw-backups"

VALIDATE=0
[[ "${1:-}" == "--validate" ]] && VALIDATE=1

OPENCLAW_CMD="/home/robby/bin/openclaw-cmd"

say() { echo "    $*"; }

cd "$STACK_DIR"
want_vw=$(docker compose config --images | grep '^vaultwarden/' | head -1)
have_vw=$(docker inspect vaultwarden --format '{{.Config.Image}}' 2>/dev/null || true)

# A just-woken host accepts SSH before dockerd is ready, and --validate runs
# immediately after the wake. Wait here too, not only in the apply phase, or a
# deploy to a sleeping host fails validation on a race rather than on a real fault.
wait_for_docker() {
    for _ in $(seq 1 30); do
        docker info >/dev/null 2>&1 && return 0
        sleep 2
    done
    echo "docker never became ready" >&2
    return 1
}

# --- validate ----------------------------------------------------------------------
if [[ $VALIDATE -eq 1 ]]; then
    wait_for_docker || exit 1
    docker compose config --quiet
    caddy_image=$(docker compose config --images | grep '^caddy' | head -1)
    if ! out=$(docker run --rm -v "$STACK_DIR/caddy:/etc/caddy:ro" "$caddy_image" \
            caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1); then
        echo "Caddyfile failed validation:" >&2
        echo "$out" >&2
        exit 1
    fi
    for f in "$HERE"/scripts/*.sh;  do [[ -e "$f" ]] && bash -n "$f"; done
    for f in "$HERE"/scripts/*.py;  do [[ -e "$f" ]] && python3 -m py_compile "$f"; done
    rm -rf "$HERE/scripts/__pycache__"
    # openclaw-cmd has no extension, so the globs above miss it, and it is the one
    # script whose breakage locks the agent out rather than just failing a job.
    if [[ -e "$HERE/scripts/openclaw-cmd" ]]; then bash -n "$HERE/scripts/openclaw-cmd"; fi
    say "compose file, Caddyfile and scripts valid"
    [[ -n "$have_vw" && "$have_vw" != "$want_vw" ]] \
        && say "vaultwarden: $have_vw -> $want_vw (vw-data is backed up first)"
    crontab -l 2>/dev/null | diff -u --label installed --label repo - "$HERE/crontab" \
        | sed 's/^/    /' || true
    exit 0
fi

# --- apply -------------------------------------------------------------------------
crontab "$HERE/crontab"
say "crontab installed"

# The agent's forced command in ~/.ssh/authorized_keys points at ~/bin/openclaw-cmd,
# not at the deployed copy, so a host that has never been deployed to still has a
# working health verb rather than an SSH key that fails closed. Deploy keeps it in
# sync; before this, it was hand-copied to three hosts and drifted.
if ! cmp -s "$HERE/scripts/openclaw-cmd" "$OPENCLAW_CMD"; then
    install -D -m 755 "$HERE/scripts/openclaw-cmd" "$OPENCLAW_CMD"
    say "openclaw-cmd updated at $OPENCLAW_CMD"
fi

# Vaultwarden's DB migrations are one-way, so a version change gets a backup first.
if [[ -n "$have_vw" && "$have_vw" != "$want_vw" ]]; then
    say "Vaultwarden $have_vw -> $want_vw: backing up vw-data first"
    docker compose pull vaultwarden
    docker compose stop vaultwarden
    mkdir -p "$VW_BACKUPS"
    backup="vw-data-$(date +%F-%H%M%S)-${have_vw##*:}.tgz"
    docker run --rm -v "$VW_DATA:/d:ro" -v "$VW_BACKUPS:/out" \
        alpine tar czf "/out/$backup" -C /d .
    say "$VW_BACKUPS/$backup"
fi

docker compose up -d

# `up` leaves caddy running when only the Caddyfile's contents changed. A reload
# applies it without dropping connections, and is a no-op when nothing changed.
for i in $(seq 1 10); do
    # If `up` just recreated caddy, its admin API may take a moment to come up.
    docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null && break
    [[ $i -eq 10 ]] && { echo "caddy reload failed" >&2; exit 1; }
    sleep 2
done
say "caddy config reloaded"

docker compose ps --format '    {{.Name}}\t{{.Image}}\t{{.Status}}'
[[ "$have_vw" != "$want_vw" ]] && docker logs vaultwarden 2>&1 | grep -m1 -i 'version' || true
