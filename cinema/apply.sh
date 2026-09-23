#!/bin/bash
# apply.sh — Apply cinema's config. Run by ../deploy.sh on cinema, from the
# deployed copy (/home/robby/homelab-deployed/cinema), never from a git clone.
#
#   apply.sh --validate   # check this copy; changes nothing (run from staging)
#   apply.sh              # install crontab, compose up
#
# No sudo, despite Plex's config living under /root/plex: those are bind mounts,
# opened by dockerd (which runs as root) and never by this script. robby is in
# cinema's docker group, so `docker compose` runs fine unprivileged. The sudo
# grant this migration was expected to need turned out not to be needed at all.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEDIA_MOUNT="/mnt/storage"
MEDIA_DIR="$MEDIA_MOUNT/plex"

VALIDATE=0
[[ "${1:-}" == "--validate" ]] && VALIDATE=1

say() { echo "    $*"; }

cd "$HERE"

# --- validate ----------------------------------------------------------------------
if [[ $VALIDATE -eq 1 ]]; then
    docker compose config --quiet
    say "compose file valid"

    # If the media disk isn't mounted, dockerd will happily create an empty
    # /mnt/storage/plex on the root disk and Plex comes up with an empty library —
    # which looks exactly like losing the library. Refuse rather than "succeed".
    if ! mountpoint -q "$MEDIA_MOUNT"; then
        echo "$MEDIA_MOUNT is not mounted; refusing (Plex would start on an empty library)" >&2
        exit 1
    fi
    [[ -d "$MEDIA_DIR" ]] || { echo "$MEDIA_DIR does not exist" >&2; exit 1; }
    say "media volume mounted: $MEDIA_DIR"

    want=$(docker compose config --images | head -1)
    have=$(docker inspect plex --format '{{.Config.Image}}' 2>/dev/null || true)
    [[ -n "$have" && "$have" != "$want" ]] && say "plex: $have -> $want"

    crontab -l 2>/dev/null | diff -u --label installed --label repo - "$HERE/crontab" \
        | sed 's/^/    /' || true
    exit 0
fi

# --- apply -------------------------------------------------------------------------
crontab "$HERE/crontab"
say "crontab installed"

# cinema is usually asleep, so a deploy often follows a Wake-on-LAN: it accepts SSH
# before dockerd is ready.
for i in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    [[ $i -eq 30 ]] && { echo "docker never became ready" >&2; exit 1; }
    sleep 2
done

docker compose up -d
docker compose ps --format '    {{.Name}}\t{{.Image}}\t{{.Status}}'
