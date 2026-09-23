#!/bin/bash
# deploy.sh — Apply this repo's bastion/ config to bastion (file).
#
# Run from any clone of the repo:
#   bastion/deploy.sh           # apply
#   bastion/deploy.sh --check   # show what would change, apply nothing
#
# Anywhere but bastion, it SSHes to bastion, fast-forwards bastion's clone to
# origin/main, and runs itself there — so what gets deployed is what's merged,
# not what's in the local tree. bastion's clone needs the git credential helper
# (see README, "Pushing to GitHub") to pull.
#
# What it applies:
#   bastion/scripts/*.sh          -> ~/scripts/  (cron runs these copies, so a
#                                    branch switch can't make a job vanish)
#   bastion/crontab               -> user crontab
#   bastion/vaultwarden/          -> docker compose up -d, run in place from the repo
#
# Only deploys a clean `main` unless --force is given: the running stack reads
# its compose file and Caddyfile from this working tree.

set -euo pipefail

BASTION_SSH="robby@192.168.0.10"
BASTION_REPO="/home/robby/Documents/Github/homelab"
BASTION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$BASTION_DIR")"
STACK_DIR="$BASTION_DIR/vaultwarden"
SCRIPTS_DEST="/home/robby/scripts"
VW_DATA="/home/robby/vaultwarden/vw-data"
VW_BACKUPS="/home/robby/vw-backups"

CHECK=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        --force) FORCE=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

say() { echo "==> $*"; }

# --- not on bastion: run there over ssh ---------------------------------------
if ! hostname -I | tr ' ' '\n' | grep -qx '192.168.0.10'; then
    say "Not on bastion — running on $BASTION_SSH"
    exec ssh "$BASTION_SSH" "cd '$BASTION_REPO' && git pull --ff-only --quiet && bastion/deploy.sh $*"
fi

# --- guard rails ---------------------------------------------------------------
branch=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)
dirty=$(git -C "$REPO_DIR" status --porcelain -- bastion)
if [[ $CHECK -eq 0 && $FORCE -eq 0 ]]; then
    if [[ "$branch" != "main" || -n "$dirty" ]]; then
        echo "Refusing to deploy: on '$branch'${dirty:+ with uncommitted changes in bastion/}." >&2
        echo "Deploy from a clean main, or pass --force if you mean it." >&2
        exit 1
    fi
fi
say "Deploying $branch @ $(git -C "$REPO_DIR" rev-parse --short HEAD)$([[ $CHECK -eq 1 ]] && echo ' (check only)')"

# --- cron scripts --------------------------------------------------------------
say "Cron scripts -> $SCRIPTS_DEST"
mkdir -p "$SCRIPTS_DEST"
for src in "$BASTION_DIR"/scripts/*.sh; do
    dest="$SCRIPTS_DEST/$(basename "$src")"
    if cmp -s "$src" "$dest"; then continue; fi
    echo "    $(basename "$src"): changed"
    [[ $CHECK -eq 1 ]] && { diff -u "$dest" "$src" || true; continue; }
    install -m 755 "$src" "$dest"
done

# --- crontab -------------------------------------------------------------------
say "Crontab"
if crontab -l 2>/dev/null | cmp -s - "$BASTION_DIR/crontab"; then
    echo "    unchanged"
elif [[ $CHECK -eq 1 ]]; then
    crontab -l 2>/dev/null | diff -u - "$BASTION_DIR/crontab" || true
else
    crontab "$BASTION_DIR/crontab"
    echo "    installed"
fi

# --- compose stack -------------------------------------------------------------
say "Compose stack (vaultwarden, caddy, adguardhome)"
cd "$STACK_DIR"
docker compose config --quiet

caddy_image=$(docker compose config --images | grep '^caddy' | head -1)
if ! out=$(docker run --rm -v "$STACK_DIR/caddy:/etc/caddy:ro" "$caddy_image" \
        caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1); then
    echo "Caddyfile failed validation:" >&2
    echo "$out" >&2
    exit 1
fi

want_vw=$(docker compose config --images | grep '^vaultwarden/' | head -1)
have_vw=$(docker inspect vaultwarden --format '{{.Config.Image}}' 2>/dev/null || true)

if [[ $CHECK -eq 1 ]]; then
    [[ -n "$have_vw" && "$have_vw" != "$want_vw" ]] && echo "    vaultwarden: $have_vw -> $want_vw (would back up vw-data first)"
    echo "    caddy: Caddyfile valid (would reload; a no-op if unchanged)"
    docker compose up -d --dry-run 2>&1 | sed 's/^/    /'
    exit 0
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
    echo "    $VW_BACKUPS/$backup"
fi

docker compose up -d

# `up` leaves caddy running when only the Caddyfile's contents changed. A reload
# applies it without dropping connections, and is a no-op when nothing changed.
say "Reloading caddy config"
for i in $(seq 1 10); do
    # If `up` just recreated caddy, its admin API may take a moment to come up.
    docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile && break
    [[ $i -eq 10 ]] && { echo "caddy reload failed" >&2; exit 1; }
    sleep 2
done

docker compose ps
[[ "$have_vw" != "$want_vw" ]] && docker logs vaultwarden 2>&1 | grep -m1 -i 'version' || true
say "Done"
