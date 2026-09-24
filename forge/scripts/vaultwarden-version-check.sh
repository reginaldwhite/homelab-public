#!/usr/bin/env bash
# vaultwarden-version-check.sh — report whether bastion's Vaultwarden has fallen behind.
#
#   vaultwarden-version-check.sh          run the check and post the report
#   vaultwarden-version-check.sh --test   post a sample report, to prove delivery works
#   vaultwarden-version-check.sh --dry-run  print the report, send nothing
#
# Posts a structured report to #homelab-logs like every other producer, and leaves the
# decision to interrupt anyone to the digester (see docs/health-digest.md). It used to
# DM the owner directly, which made it one more thing deciding severity for itself.
#
# Why this exists: the Vaultwarden image tag is pinned deliberately, so nothing upgrades
# it on its own. Bitwarden clients refuse to sync against a server that is too old and
# report it as an opaque decoding error, not a version warning — so being stale has to be
# surfaced here rather than discovered from a phone that won't sync.
#
# Runs on forge from the user crontab (@reboot). bastion wakes forge at 5am and shuts it
# down at 11pm, so "at boot" is effectively a once-a-day check.
#
# Note: restarting a container does NOT update it. Only `docker compose pull` fetches a
# new image. This reports when to do that; it deliberately does not do it itself, because
# Vaultwarden's database migrations are not reversible.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$(dirname "$HERE")/.env"
BASTION_HOST="robby@192.168.0.10"
SSH_KEY="/home/robby/.ssh/id_ed25519"
RELEASES_URL="https://api.github.com/repos/dani-garcia/vaultwarden/releases/latest"
LOG="/home/robby/logs/vaultwarden-version-check.log"

LOGS_CHANNEL="C0C3XSNE71T"      # #homelab-logs
BOT_MENTION="<@U0AJY4AETS7>"    # openclaw

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Iseconds) [vw-check] $*" >> "$LOG"; }

# An exported value wins over the file, which is what makes this testable.
if [[ -r "$ENV_FILE" ]]; then
    _tok=${SLACK_BOT_TOKEN:-} _hook=${SLACK_LOGS_WEBHOOK_URL:-}
    set -a; . "$ENV_FILE"; set +a
    SLACK_BOT_TOKEN=${_tok:-${SLACK_BOT_TOKEN:-}}
    SLACK_LOGS_WEBHOOK_URL=${_hook:-${SLACK_LOGS_WEBHOOK_URL:-}}
fi

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

# --- posting -----------------------------------------------------------------------
# Posts directly rather than through the openclaw container. That used to mean waiting
# up to ten minutes at boot for the container to come up, purely because it was the
# sending mechanism; the real dependency is the network, which is waited for below.
post() {
    local text=$1 payload resp
    if [[ $DRY -eq 1 ]]; then printf '%s\n' "$text"; return 0; fi
    if [[ -n "${SLACK_LOGS_WEBHOOK_URL:-}" ]]; then
        payload=$(jq -n --arg t "$text" '{text:$t}')
        resp=$(curl -sS --max-time 20 -X POST -H 'Content-type: application/json' \
                    --data "$payload" "$SLACK_LOGS_WEBHOOK_URL" 2>&1)
        [[ "$resp" == "ok" ]] && { log "posted via webhook"; return 0; }
    elif [[ -n "${SLACK_BOT_TOKEN:-}" ]]; then
        payload=$(jq -n --arg c "$LOGS_CHANNEL" --arg t "$text" '{channel:$c,text:$t}')
        resp=$(curl -sS --max-time 20 -X POST https://slack.com/api/chat.postMessage \
                    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
                    -H 'Content-type: application/json; charset=utf-8' \
                    --data "$payload" 2>&1)
        [[ "$(jq -r '.ok // false' <<<"$resp" 2>/dev/null)" == "true" ]] \
            && { log "posted"; return 0; }
    else
        log "ERROR: no SLACK_LOGS_WEBHOOK_URL or SLACK_BOT_TOKEN in $ENV_FILE"
        return 1
    fi
    log "ERROR: Slack rejected the post: ${resp:0:300}"
    return 1
}

# Builds the report. Arguments: status, then any number of "check|detail" findings.
report() {
    local status=$1; shift
    local findings="[]" n=0
    for pair in "$@"; do
        findings=$(jq -c --arg c "${pair%%|*}" --arg d "${pair#*|}" \
                      '. + [{check:$c, detail:$d}]' <<<"$findings")
        n=$((n + 1))
    done
    local body
    body=$(jq -n --arg h vaultwarden --arg s "$status" --arg t "$(date -Iseconds)" \
                 --argjson f "$findings" --arg dep "${deployed:-unknown}" \
                 --arg lat "${latest:-unknown}" \
       '{host:"bastion", kind:"vaultwarden-version", status:$s, ts:$t, findings:$f,
         metrics:{deployed:$dep, latest:$lat}}')
    post "$BOT_MENTION bastion vaultwarden-version: $status ($n finding$([[ $n == 1 ]] || echo s))
\`\`\`json
$body
\`\`\`"
}

# --- --test ------------------------------------------------------------------------
# This job is silent for months at a time, which is how a broken notifier goes unnoticed.
if [[ "${1:-}" == "--test" ]]; then
    log "test report requested"
    deployed="0.0.0-test"; latest="0.0.0-test"
    report ok "delivery test|This is a test report, not a real version check."
    exit $?
fi

# --- wait for the network ----------------------------------------------------------
# @reboot can fire before the network is usable; a check that could not look is not an
# all clear, so it waits rather than reporting a false failure.
latest=""
for _ in $(seq 1 30); do
    latest=$(curl -fsS --max-time 20 "$RELEASES_URL" 2>/dev/null \
        | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
    [[ -n "$latest" ]] && break
    sleep 10
done

# --- what bastion is actually running (not what the repo intends) -------------------
deployed=$(ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$SSH_KEY" "$BASTION_HOST" \
    "docker inspect vaultwarden --format '{{.Config.Image}}'" 2>/dev/null \
    | tr -d '\r' | sed 's/.*://')

if [[ -z "$latest" || -z "$deployed" ]]; then
    unreachable="GitHub"
    [[ -n "$latest" ]] && unreachable="bastion"
    log "ERROR: check failed (latest='$latest' deployed='$deployed')"
    report error "version check|Could not reach ${unreachable}. See ${LOG} on forge."
    exit 1
fi

if [[ "$deployed" == "latest" ]]; then
    log "WARN: bastion is on the ':latest' tag"
    report warn "image tag|Vaultwarden is on the :latest tag, so its real version cannot be checked. Newest release is ${latest}. Pin the tag in bastion/vaultwarden/docker-compose.yml."
    exit 0
fi

if [[ "$deployed" != "$latest" ]]; then
    log "behind: deployed=$deployed latest=$latest"
    report warn "version|Vaultwarden on bastion is ${deployed}; ${latest} is available. Bitwarden clients refuse to sync against a server that is too old and fail with an opaque decoding error. To upgrade: bump the tag in bastion/vaultwarden/docker-compose.yml, merge, then ./deploy.sh bastion (it backs up vw-data first)."
    exit 0
fi

log "up to date: $deployed"
report ok
