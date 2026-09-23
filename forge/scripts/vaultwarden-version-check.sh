#!/usr/bin/env bash
# vaultwarden-version-check.sh — Slack a warning when bastion's Vaultwarden falls behind.
#
# Runs on forge from the user crontab (@reboot). bastion wakes forge at 5am and shuts it
# down at 11pm, so "at boot" is effectively a once-a-day check — and it only ever runs
# while OpenClaw, which does the sending, is actually up. Running it on bastion instead
# would mean firing at times when forge (and therefore Slack delivery) is unavailable.
#
# Why this exists: the Vaultwarden image tag is pinned deliberately, so nothing upgrades
# it on its own. Bitwarden clients refuse to sync against a server that is too old and
# report it as an opaque decoding error, not a version warning — so being stale has to be
# surfaced here rather than discovered from a phone that won't sync.
#
# Note: restarting a container does NOT update it. Only `docker compose pull` fetches a
# new image. This script tells you when to do that; it deliberately does not do it itself,
# because Vaultwarden's database migrations are not reversible.

set -uo pipefail

BASTION_HOST="robby@192.168.0.10"
SSH_KEY="/home/robby/.ssh/id_ed25519"
SLACK_TARGET="U01E91JBPPG"
RELEASES_URL="https://api.github.com/repos/dani-garcia/vaultwarden/releases/latest"
LOG="/home/robby/logs/vaultwarden-version-check.log"

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Iseconds) [vw-check] $*" >> "$LOG"; }

notify() {
    if docker exec openclaw node openclaw.mjs message send \
        --channel slack --target "$SLACK_TARGET" --message "$1" >/dev/null 2>&1; then
        log "notified Slack"
    else
        log "ERROR: could not send Slack message"
    fi
}

# --- wait for openclaw, since it is what delivers the alert -------------------
# At boot, docker and the container need time; give it up to 10 minutes.
ready=0
for _ in $(seq 1 60); do
    if docker exec openclaw true 2>/dev/null; then ready=1; break; fi
    sleep 10
done
if [[ $ready -eq 0 ]]; then
    log "ERROR: openclaw not reachable after 10 minutes, cannot notify — giving up"
    exit 1
fi

# --- --test: prove the alert path works without waiting for a real upgrade -----
# This job is silent for months at a time, which is how a broken notifier goes
# unnoticed. `--test` sends a sample alert so delivery can be re-verified on demand.
if [[ "${1:-}" == "--test" ]]; then
    log "test alert requested"
    notify ":white_check_mark: Vaultwarden version check is wired up and can reach you here. This is a test — a real alert fires when bastion's Vaultwarden falls behind the latest release."
    exit 0
fi

# --- latest upstream release --------------------------------------------------
latest=$(curl -fsS --max-time 20 "$RELEASES_URL" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)

# --- what bastion is actually running (not what the repo intends) -------------
deployed=$(ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$SSH_KEY" "$BASTION_HOST" \
    "docker inspect vaultwarden --format '{{.Config.Image}}'" 2>/dev/null \
    | tr -d '\r' | sed 's/.*://')

# A silently broken checker is the failure mode this whole script exists to
# prevent, so a failed check is itself worth an alert.
if [[ -z "$latest" || -z "$deployed" ]]; then
    unreachable="GitHub"
    [[ -n "$latest" ]] && unreachable="bastion"
    log "ERROR: check failed (latest='$latest' deployed='$deployed')"
    notify ":warning: Vaultwarden version check failed on forge — could not reach ${unreachable}. See ${LOG} on forge."
    exit 1
fi

if [[ "$deployed" == "latest" ]]; then
    log "WARN: bastion is on the ':latest' tag; real version cannot be determined"
    notify ":warning: Vaultwarden on bastion is using the \`:latest\` tag, so its real version can't be checked. Newest release is ${latest}. Pin the tag in Homelab/bastion/vaultwarden/docker-compose.yml."
    exit 0
fi

if [[ "$deployed" != "$latest" ]]; then
    log "behind: deployed=$deployed latest=$latest"
    notify ":rotating_light: Vaultwarden on bastion is ${deployed} — ${latest} is available.

Bitwarden clients refuse to sync against a server that is too old, and fail with an opaque decoding error rather than a version warning.

To upgrade: bump the tag in Homelab/bastion/vaultwarden/docker-compose.yml and merge to main, then on bastion:
git pull && bastion/deploy.sh    (backs up vw-data before upgrading)"
else
    log "up to date: $deployed"
fi
