#!/bin/bash
# apply.sh — Apply forge's config. Run by ../deploy.sh on forge, from the
# deployed copy (/home/robby/homelab-deployed/forge), never from a git clone.
#
#   apply.sh --validate   # check this copy; changes nothing (run from staging)
#   apply.sh              # install crontab, sync openclaw config, compose up
#
# The agent's live state lives at $STATE, outside both the repo and the deployed
# copy. Deploy copies config files INTO it and never deletes from it: sessions,
# memory, workspace, identity, devices and credentials only exist there.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="/home/robby/openclaw-state"

VALIDATE=0
[[ "${1:-}" == "--validate" ]] && VALIDATE=1

OPENCLAW_CMD="/home/robby/bin/openclaw-cmd"

say() { echo "    $*"; }

# Config files that deploy owns, as "source:destination-under-$STATE".
CONFIG=(
    "openclaw/openclaw.json:openclaw.json"
    "openclaw/models.json:agents/main/agent/models.json"
    "openclaw/scripts/forge-power.py:scripts/forge-power.py"
    "openclaw/scripts/plex-power.py:scripts/plex-power.py"
)

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
    cd "$HERE"
    docker compose config --quiet
    python3 -c "import json,sys; [json.load(open(f)) for f in sys.argv[1:]]" \
        openclaw/openclaw.json openclaw/models.json
    for f in openclaw/scripts/*.py; do python3 -m py_compile "$f"; done
    rm -rf openclaw/scripts/__pycache__
    for f in scripts/*.sh; do [[ -e "$f" ]] && bash -n "$f"; done
    for f in scripts/*.py; do [[ -e "$f" ]] && python3 -m py_compile "$f"; done
    rm -rf scripts/__pycache__
    # openclaw-cmd has no extension, so the globs above miss it, and it is the one
    # script whose breakage locks the agent out rather than just failing a job.
    if [[ -e "$HERE/scripts/openclaw-cmd" ]]; then bash -n "$HERE/scripts/openclaw-cmd"; fi
    say "compose file, JSON config and scripts valid"

    # OpenClaw reads its config at startup, so a changed file means a restart.
    for pair in "${CONFIG[@]}"; do
        src="$HERE/${pair%%:*}" dst="$STATE/${pair##*:}"
        cmp -s "$src" "$dst" || say "openclaw config differs: ${pair##*:} (agent will be restarted)"
    done
    crontab -l 2>/dev/null | diff -u --label installed --label repo - "$HERE/crontab" \
        | sed 's/^/    /' || true
    exit 0
fi

# --- apply -------------------------------------------------------------------------
cd "$HERE"

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

# A just-woken host accepts SSH before dockerd is ready.
wait_for_docker || exit 1

# Copy config in; never delete. Track whether the agent needs a restart.
openclaw_changed=0
for pair in "${CONFIG[@]}"; do
    src="$HERE/${pair%%:*}" dst="$STATE/${pair##*:}"
    if ! cmp -s "$src" "$dst"; then
        mkdir -p "$(dirname "$dst")"
        cp -p "$src" "$dst"
        say "updated ${pair##*:}"
        openclaw_changed=1
    fi
done
[[ $openclaw_changed -eq 0 ]] && say "openclaw config unchanged"

docker compose up -d

# `up -d` won't restart openclaw when only a file inside the bind mount changed,
# and OpenClaw caches its config at startup — so a config-only change would
# otherwise deploy cleanly and take effect never.
if [[ $openclaw_changed -eq 1 ]]; then
    docker compose restart openclaw
    say "openclaw restarted to pick up new config"
fi

docker compose ps --format '    {{.Name}}\t{{.Image}}\t{{.Status}}'
