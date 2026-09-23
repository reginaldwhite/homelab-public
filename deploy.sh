#!/bin/bash
# deploy.sh — Push a host's folder from this repo to that host and apply it.
#
#   ./deploy.sh bastion            # deploy one host
#   ./deploy.sh all                # every migrated host
#   ./deploy.sh --check bastion    # validate and list changed files, apply nothing
#
# Hosts get a deployed copy, never a git clone, at $DEST/<host>/. A branch switch
# in any clone therefore changes nothing running; only a deploy does. Each host
# folder has an apply.sh that runs on the host from that deployed copy.
#
# Per host:
#   1. rsync <host>/ to $DEST/.staging/<host>/ on the host
#   2. <staging>/apply.sh --validate           (compose config, Caddyfile, ...)
#   3. list what would change in $DEST/<host>/  (--check stops here)
#   4. rsync staging -> $DEST/<host>/, then run its apply.sh
# A change that fails validation never reaches the live copy.
#
# Deploys only a clean checkout of origin/main (--force overrides), so what runs
# is what's merged. Needs SSH key auth to each remote host; the host this runs
# on is deployed locally. `.env` files are never pushed and survive deploys.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="/home/robby/homelab-deployed"

declare -A HOST_SSH=(
    [bastion]="robby@192.168.0.10"
    [forge]="robby@192.168.0.12"
    [cinema]="robby@192.168.0.13"
)
# Hosts whose folder has an apply.sh and a stack that runs from $DEST.
# forge and cinema still run from hand-managed copies; see README.
MIGRATED=(bastion)

CHECK=0
FORCE=0
HOSTS=()
for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        --force) FORCE=1 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        all) HOSTS+=("${MIGRATED[@]}") ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) HOSTS+=("$arg") ;;
    esac
done
[[ ${#HOSTS[@]} -gt 0 ]] || { sed -n '2,6p' "$0" >&2; exit 2; }

say() { echo "==> $*"; }
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=5)

# --- only deploy what's merged -----------------------------------------------------
git -C "$REPO_DIR" fetch --quiet origin main
head=$(git -C "$REPO_DIR" rev-parse HEAD)
merged=$(git -C "$REPO_DIR" rev-parse origin/main)
if [[ $FORCE -eq 0 && $CHECK -eq 0 && "$head" != "$merged" ]]; then
    echo "Refusing to deploy: HEAD ($(git -C "$REPO_DIR" rev-parse --short HEAD)) is not origin/main." >&2
    echo "Merge first and 'git pull', or pass --force if you mean it." >&2
    exit 1
fi

is_local() {
    local ip=${HOST_SSH[$1]#*@}
    hostname -I | tr ' ' '\n' | grep -qx "$ip"
}

# Run a command string on the host: locally, or over ssh.
on_host() {
    local host=$1; shift
    if is_local "$host"; then bash -c "$*"; else ssh "${SSH_OPTS[@]}" "${HOST_SSH[$host]}" "$*"; fi
}

deploy_host() {
    local host=$1
    local live="$DEST/$host" staging="$DEST/.staging/$host"
    local rsync_dest

    if [[ -z "${HOST_SSH[$host]:-}" ]]; then
        echo "unknown host: $host (known: ${!HOST_SSH[*]})" >&2; return 1
    fi
    if [[ ! " ${MIGRATED[*]} " == *" $host "* ]]; then
        echo "$host is not migrated to deploy.sh yet; see README." >&2; return 1
    fi
    if [[ $FORCE -eq 0 && $CHECK -eq 0 && -n "$(git -C "$REPO_DIR" status --porcelain -- "$host")" ]]; then
        echo "Refusing to deploy $host: uncommitted changes in $host/." >&2; return 1
    fi

    say "$host (${HOST_SSH[$host]}) @ $(git -C "$REPO_DIR" rev-parse --short HEAD)$([[ $CHECK -eq 1 ]] && echo ', check only')"
    if is_local "$host"; then
        rsync_dest="$staging/"
    else
        if ! ssh "${SSH_OPTS[@]}" "${HOST_SSH[$host]}" true 2>/dev/null; then
            echo "$host is unreachable (asleep?)" >&2; return 1
        fi
        rsync_dest="${HOST_SSH[$host]}:$staging/"
    fi

    # set -e doesn't apply inside a function called from `||`, hence the explicit returns.
    on_host "$host" "mkdir -p '$staging' '$live'" || return 1
    rsync -a --delete --exclude=.env -e "ssh ${SSH_OPTS[*]}" "$REPO_DIR/$host/" "$rsync_dest" || return 1

    on_host "$host" "'$staging/apply.sh' --validate" || { echo "$host: validation failed, nothing applied" >&2; return 1; }

    echo "    files that would change in $live:"
    on_host "$host" "rsync -ani --delete --exclude=.env '$staging/' '$live/' | sed 's/^/      /'" || return 1
    [[ $CHECK -eq 1 ]] && return 0

    on_host "$host" "rsync -a --delete --exclude=.env '$staging/' '$live/' && '$live/apply.sh'" || return 1
}

failed=()
for host in "${HOSTS[@]}"; do
    deploy_host "$host" || failed+=("$host")
done
if [[ ${#failed[@]} -gt 0 ]]; then
    echo "FAILED: ${failed[*]}" >&2
    exit 1
fi
say "Done"
