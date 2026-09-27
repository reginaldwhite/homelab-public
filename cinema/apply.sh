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

OPENCLAW_CMD="/home/robby/bin/openclaw-cmd"

say() { echo "    $*"; }

cd "$HERE"

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
    say "compose file valid"

    # If the media disk isn't mounted, dockerd will happily create an empty
    # /mnt/storage/plex on the root disk and Plex comes up with an empty library —
    # which looks exactly like losing the library. Refuse rather than "succeed".
    if ! mountpoint -q "$MEDIA_MOUNT"; then
        echo "$MEDIA_MOUNT is not mounted; refusing (Plex would start on an empty library)" >&2
        exit 1
    fi
    [[ -d "$MEDIA_DIR" ]] || { echo "$MEDIA_DIR does not exist" >&2; exit 1; }
    # openclaw-cmd has no extension, so the globs above miss it, and it is the one
    # script whose breakage locks the agent out rather than just failing a job.
    if [[ -e "$HERE/scripts/openclaw-cmd" ]]; then bash -n "$HERE/scripts/openclaw-cmd"; fi
    say "media volume mounted: $MEDIA_DIR"

    for f in "$HERE"/scripts/*.py; do [[ -e "$f" ]] && python3 -m py_compile "$f"; done
    rm -rf "$HERE/scripts/__pycache__"

    # Plex specifically: the exporters share this compose file now, and `head -1` picked
    # cAdvisor and reported a phantom Plex upgrade on every deploy.
    want=$(docker compose config --images | grep '^plexinc/' | head -1)
    have=$(docker inspect plex --format '{{.Config.Image}}' 2>/dev/null || true)
    [[ -n "$have" && "$have" != "$want" ]] && say "plex: $have -> $want"

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

# cinema is usually asleep, so a deploy often follows a Wake-on-LAN: it accepts SSH
# before dockerd is ready.
wait_for_docker || exit 1

docker compose up -d
# Alloy's config is a bind mount, which `up -d` does not watch. Restart it when the shared
# file's content changed since the last apply (bastion does the same for its whole stack).
if [[ -e "$HERE/scripts/alloy/config.alloy" ]] && docker inspect alloy >/dev/null 2>&1; then
    applied="/home/robby/.alloy-config.sha256"
    now=$(sha256sum "$HERE/scripts/alloy/config.alloy" | cut -c1-64)
    if [[ "$(cat "$applied" 2>/dev/null)" != "$now" ]]; then
        docker compose restart alloy >/dev/null && say "alloy restarted for new config"
        echo "$now" > "$applied"
    fi
fi

docker compose ps --format '    {{.Name}}\t{{.Image}}\t{{.Status}}'
