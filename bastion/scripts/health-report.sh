#!/usr/bin/env bash
# health-report.sh — bastion posts its own health to #homelab-status, once a day.
#
#   health-report.sh          # run the checks and post the result
#   health-report.sh --test   # post a sample alert to prove delivery still works
#
# Why bastion reports for itself: the other monitor runs on forge, which is down
# 11pm-5am by design and was down for two months in mid-2026. A check that only
# runs while forge is up cannot say anything about bastion during the hours forge
# is asleep, and bastion is the host that runs DNS and the vault for the house.
# This path depends on nothing but bastion and its network.
#
# It does NOT replace forge's check, and the overlap is deliberate: forge's is an
# external check, so it can report bastion being down or wedged — which bastion
# can never do for itself. A host that is unwell enough to stop running cron also
# stops self-reporting, and silence is not an alert. The two cover different
# failures, so both are kept.
#
# Credentials come from the deployed copy's .env, which is never in git and never
# pushed by deploy.sh. Either of:
#   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...   (preferred: post-only,
#                                                             single channel)
#   SLACK_BOT_TOKEN=xoxb-...  and  SLACK_CHANNEL=C0C3MAXLSRK
# The webhook is worth preferring because this host runs the password vault and a
# bot token can also read channel history; a webhook can only post.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$(dirname "$HERE")/.env"
HEALTH_CMD="/home/robby/bin/openclaw-cmd"
MENTION="<@U01E91JBPPG>"     # robbywhite; must reach Slack with the angle brackets
EXPECTED_SECTIONS=5
LOG="/home/robby/logs/health-report.log"

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Iseconds) [health-report] $*" >> "$LOG"; }

# --- credentials ---------------------------------------------------------------
# An already-exported value wins over the file, matching scripts/git-credential-homelab.
# That is also what makes this script testable: a one-off run can point it somewhere
# else without editing the deployed .env.
_env_hook=${SLACK_WEBHOOK_URL:-} _env_tok=${SLACK_BOT_TOKEN:-} _env_chan=${SLACK_CHANNEL:-}
if [[ -r "$ENV_FILE" ]]; then
    set -a; . "$ENV_FILE"; set +a
fi
SLACK_WEBHOOK_URL=${_env_hook:-${SLACK_WEBHOOK_URL:-}}
SLACK_BOT_TOKEN=${_env_tok:-${SLACK_BOT_TOKEN:-}}
SLACK_CHANNEL=${_env_chan:-${SLACK_CHANNEL:-C0C3MAXLSRK}}

post() {
    local text=$1 payload resp
    if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
        payload=$(jq -n --arg t "$text" '{text:$t}')
        resp=$(curl -sS --max-time 20 -X POST -H 'Content-type: application/json' \
                    --data "$payload" "$SLACK_WEBHOOK_URL" 2>&1)
        [[ "$resp" == "ok" ]] && { log "posted via webhook"; return 0; }
    elif [[ -n "${SLACK_BOT_TOKEN:-}" ]]; then
        payload=$(jq -n --arg c "$SLACK_CHANNEL" --arg t "$text" '{channel:$c,text:$t}')
        resp=$(curl -sS --max-time 20 -X POST https://slack.com/api/chat.postMessage \
                    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
                    -H 'Content-type: application/json; charset=utf-8' \
                    --data "$payload" 2>&1)
        [[ "$(jq -r '.ok // false' <<<"$resp" 2>/dev/null)" == "true" ]] \
            && { log "posted via chat.postMessage"; return 0; }
    else
        log "ERROR: no SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN in $ENV_FILE — cannot post"
        return 1
    fi
    # The reason this job exists is that a failure nobody sees is not a report, so
    # a failed post is itself recorded rather than swallowed.
    log "ERROR: Slack rejected the post: ${resp:0:300}"
    return 1
}

# --- --test: prove delivery without waiting for something to break --------------
if [[ "${1:-}" == "--test" ]]; then
    log "test post requested"
    post "$MENTION :test_tube: bastion self-check delivery test — this is what an alert will look like. Nothing is wrong."
    exit $?
fi

# --- run the checks -------------------------------------------------------------
# Reuse the same `health` verb the agent's monitor runs, so both reports can never
# disagree about what "healthy" means. It is a forced-command wrapper, so it wants
# the verb in SSH_ORIGINAL_COMMAND as well as in argv.
if [[ ! -x "$HEALTH_CMD" ]]; then
    log "ERROR: $HEALTH_CMD is missing or not executable"
    post "$MENTION :x: bastion self-check could not run: $HEALTH_CMD is missing on bastion. The agent's health verb is broken too — reinstall it with \`install -m 755 scripts/openclaw-cmd ~/bin/openclaw-cmd\`."
    exit 1
fi

out=$(SSH_ORIGINAL_COMMAND=health "$HEALTH_CMD" health 2>&1)
rc=$?

if [[ $rc -ne 0 ]]; then
    log "ERROR: health verb exited $rc"
    post "$MENTION :x: bastion self-check could not run: the health verb exited $rc.
\`\`\`
${out:0:2000}
\`\`\`"
    exit 1
fi

# A truncated run must never read as healthy, so the section count is checked too.
sections=$(grep -c '^== ' <<<"$out")
if [[ "$sections" -ne "$EXPECTED_SECTIONS" ]]; then
    log "ERROR: expected $EXPECTED_SECTIONS sections, got $sections"
    post "$MENTION :x: bastion self-check could not run: expected $EXPECTED_SECTIONS sections, got $sections. A check that could not look is never an all clear.
\`\`\`
${out:0:2000}
\`\`\`"
    exit 1
fi

# Every line that is neither a section header nor the word "none" is something to
# report. That includes CHECK FAILED, which the health verb prints when a check
# itself errors.
findings=$(grep -v '^== ' <<<"$out" | grep -vx 'none' | grep -c .)

if [[ "$findings" -eq 0 ]]; then
    log "all clear"
    post ":white_check_mark: bastion self-check: all clear"
else
    log "$findings finding(s) reported"
    post "$MENTION :warning: bastion self-check found something:
\`\`\`
${out:0:2000}
\`\`\`"
fi
