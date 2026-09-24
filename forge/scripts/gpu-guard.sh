#!/usr/bin/env bash
# gpu-guard.sh — keep ollama actually running on the GPU.
#
#   gpu-guard.sh            check, and repair if needed
#   gpu-guard.sh --dry-run  report what it would do, change nothing
#
# The problem this exists for, observed on 2026-09-24:
#
#   05:00:40  docker starts, ollama starts and finds the RTX 5060 Ti
#   05:05:43  the GDM login screen comes up and unloads the nvidia kernel modules
#             ("rmmod: ERROR: Module nvidia is in use by: nvidia_uvm" in the journal)
#   06:36     first model load falls back to GPULayers:[] — 20B params on CPU
#
# The container keeps running and looks healthy; `nvidia-smi` on the *host* still
# works. Only `docker exec ollama nvidia-smi` fails, with "Failed to initialize
# NVML: Unknown Error". Inference silently moves to the CPU, where a 20B model
# answers in minutes instead of seconds — which reads as "the agent stopped
# responding to Slack", not as a GPU fault.
#
# Restarting the ollama container restores it. This checks every quarter hour and
# does that, then reloads the model so the next request is not a cold start.
#
# This is a mitigation, not the cure. The cure is to stop running a graphical
# login manager on a headless server:
#
#   sudo systemctl set-default multi-user.target && sudo systemctl disable gdm3
#
# which also removes the i915 hotplug churn and the desktop-session noise that
# openclaw-cmd's NOISE list exists to filter. Left as a decision rather than done,
# because it changes what you get when you sit down at the machine.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$(dirname "$HERE")/.env"
STACK_DIR="$HERE/.."
MODEL="gpt-oss:20b"
LOG="/home/robby/logs/gpu-guard.log"

LOGS_CHANNEL="C0C3XSNE71T"      # #homelab-logs
BOT_MENTION="<@U0AJY4AETS7>"    # openclaw

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Iseconds) [gpu-guard] $*" >> "$LOG"; }

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

if [[ -r "$ENV_FILE" ]]; then
    _tok=${SLACK_BOT_TOKEN:-} _hook=${SLACK_LOGS_WEBHOOK_URL:-}
    set -a; . "$ENV_FILE"; set +a
    SLACK_BOT_TOKEN=${_tok:-${SLACK_BOT_TOKEN:-}}
    SLACK_LOGS_WEBHOOK_URL=${_hook:-${SLACK_LOGS_WEBHOOK_URL:-}}
fi

# Reports only when it acted or could not act. Staying quiet the rest of the time is
# the point: this runs four times an hour. But a self-healing loop that heals
# silently hides a recurring fault, so every repair is reported and lands in the
# daily digest even though it needed no one's attention.
report() {
    local status=$1 detail=$2 body text
    body=$(jq -n --arg s "$status" --arg t "$(date -Iseconds)" --arg d "$detail" \
        '{host:"forge", kind:"gpu-guard", status:$s, ts:$t,
          findings:[{check:"ollama gpu access", detail:$d}], metrics:{}}')
    text="$BOT_MENTION forge gpu-guard: $status (1 finding)
\`\`\`json
$body
\`\`\`"
    if [[ $DRY -eq 1 ]]; then printf '%s\n' "$text"; return 0; fi
    local payload resp
    if [[ -n "${SLACK_LOGS_WEBHOOK_URL:-}" ]]; then
        payload=$(jq -n --arg t "$text" '{text:$t}')
        resp=$(curl -sS --max-time 20 -X POST -H 'Content-type: application/json' \
                    --data "$payload" "$SLACK_LOGS_WEBHOOK_URL" 2>&1)
        [[ "$resp" == "ok" ]] && return 0
    elif [[ -n "${SLACK_BOT_TOKEN:-}" ]]; then
        payload=$(jq -n --arg c "$LOGS_CHANNEL" --arg t "$text" '{channel:$c,text:$t}')
        resp=$(curl -sS --max-time 20 -X POST https://slack.com/api/chat.postMessage \
                    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
                    -H 'Content-type: application/json; charset=utf-8' \
                    --data "$payload" 2>&1)
        [[ "$(jq -r '.ok // false' <<<"$resp" 2>/dev/null)" == "true" ]] && return 0
    fi
    log "ERROR: could not post: ${resp:-no credentials}"
    return 1
}

# Two ways this breaks, and both matter:
#   1. the container cannot reach the driver at all (NVML error)
#   2. it can, but a model was loaded while it could not, so it is resident on CPU
#      and OLLAMA_KEEP_ALIVE=-1 means it stays there forever
gpu_visible() { docker exec ollama nvidia-smi -L >/dev/null 2>&1; }
model_on_cpu() {
    local ps
    ps=$(docker exec ollama ollama ps 2>/dev/null) || return 1
    grep -qE '[0-9]+% +CPU' <<<"$ps"
}

if ! docker inspect ollama >/dev/null 2>&1; then
    log "ollama container does not exist; nothing to guard"
    exit 0
fi

reason=""
if ! gpu_visible; then
    reason="the ollama container cannot reach the GPU (NVML error) while the host can"
elif model_on_cpu; then
    reason="a model is resident on the CPU, so it was loaded while the GPU was unreachable"
fi

if [[ -z "$reason" ]]; then
    exit 0
fi

log "DEGRADED: $reason"
if [[ $DRY -eq 1 ]]; then
    report warn "would restart ollama: $reason"
    exit 0
fi

cd "$STACK_DIR" || { log "ERROR: no stack dir $STACK_DIR"; exit 1; }
docker restart ollama >/dev/null 2>&1

# Wait for the API before asking it to load anything.
ready=0
for _ in $(seq 1 30); do
    docker exec ollama ollama list >/dev/null 2>&1 && { ready=1; break; }
    sleep 5
done
if [[ $ready -eq 0 ]]; then
    log "ERROR: ollama did not come back after restart"
    report error "ollama lost GPU access and did not come back after a restart. $reason"
    exit 1
fi

# Reload so the next request is not a cold start; OLLAMA_KEEP_ALIVE=-1 keeps it.
docker exec ollama ollama run "$MODEL" "" >/dev/null 2>&1

if ! gpu_visible; then
    log "ERROR: still no GPU after restart"
    report error "ollama still cannot reach the GPU after a restart. Check the nvidia modules on forge; a reboot may be needed."
    exit 1
fi
if model_on_cpu; then
    log "ERROR: model still on CPU after restart"
    report error "ollama reached the GPU after a restart but $MODEL is still resident on the CPU."
    exit 1
fi

log "recovered: ollama restarted and $MODEL is back on the GPU"
report warn "ollama had lost GPU access and was restarted; $MODEL is back on the GPU. Cause: $reason. Root fix is to stop running GDM on forge — see the header of gpu-guard.sh."
