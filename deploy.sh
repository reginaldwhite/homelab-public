#!/usr/bin/env bash
# deploy.sh — Push a host's folder from this repo to that host and apply it.
#
#   ./deploy.sh bastion            # deploy one host
#   ./deploy.sh all                # every migrated host
#   ./deploy.sh --check bastion    # validate and list changed files, apply nothing
#   ./deploy.sh --no-wake cinema   # never send Wake-on-LAN; fail if it is asleep
#
# Hosts get a deployed copy, never a git clone, at $DEST/<host>/. A branch switch
# in any clone therefore changes nothing running; only a deploy does. Each host
# folder has an apply.sh that runs on the host from that deployed copy.
#
# Per host:
#   1. wake it if it is asleep and has a MAC below, then wait for SSH
#   2. rsync <host>/ to $DEST/.staging/<host>/ on the host, then merge shared/
#      into its scripts/ (files that are identical on every host live there once)
#   3. <staging>/apply.sh --validate           (compose config, Caddyfile, ...)
#   4. list what would change in $DEST/<host>/  (--check stops here)
#   5. rsync staging -> $DEST/<host>/, then run its apply.sh
# A change that fails validation never reaches the live copy.
#
# --check is not read-only. It still writes the staging directory on the host and
# runs apply.sh --validate out of it, which starts a throwaway container to check
# the Caddyfile. It also skips the clean-checkout gate, deliberately, so you can
# check work in progress — which means --check can execute uncommitted code on the
# host. It changes nothing in $DEST/<host>/, and nothing running.
#
# Deploys only a clean checkout of origin/main (--force overrides), so what runs
# is what's merged. Needs SSH key auth to each remote host; the host this runs
# on is deployed locally. Secrets and anything git ignores are never pushed, and
# the copies on the host survive deploys.

set -euo pipefail

# macOS still ships bash 3.2 (2007), which has neither associative arrays nor
# mapfile — both used below. `env bash` picks up a newer one from PATH if there is
# one; without this check the failure on a Mac is "bastion: unbound variable" from
# the HOST_SSH declaration, which says nothing about the actual problem. Deploying
# from a laptop is a documented path, so it should fail legibly.
if (( BASH_VERSINFO[0] < 4 )); then
    echo "deploy.sh needs bash 4 or newer; this is bash ${BASH_VERSION}." >&2
    echo "On macOS: brew install bash   (/bin/bash is 3.2 and cannot run this)." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="/home/robby/homelab-deployed"

declare -A HOST_SSH=(
    [bastion]="robby@192.168.0.10"
    [forge]="robby@192.168.0.12"
    [cinema]="robby@192.168.0.13"
    [vault]="root@192.168.0.212"
)
# Hosts whose folder has an apply.sh and a stack that runs from $DEST.
MIGRATED=(bastion forge cinema)

# Why a known host is not on that list. vault is the permanent case, not a to-do.
declare -A NOT_MIGRATED=(
    [vault]="vault is deliberately not deployed to: its root filesystem is RAM (wiped
     every reboot, and it reboots weekly on the smart plug) and its flash is vfat,
     which cannot carry an executable bit — so a deployed apply.sh could neither
     survive nor run. It is managed from bastion instead; see README."
)

# Hosts that sleep and answer Wake-on-LAN. bastion is always on; vault has no WoL
# support at all and is powered by a smart plug on a fixed schedule.
declare -A HOST_MAC=(
    [forge]="88:d7:f6:13:14:3a"
    [cinema]="d0:17:c2:dd:c1:c2"
)
WAKE_TIMEOUT=180   # seconds; bastion is old and forge's POST is slow

CHECK=0
FORCE=0
WAKE=1
HOSTS=()
for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        --force) FORCE=1 ;;
        --no-wake) WAKE=0 ;;
        # The whole header block, however long it grows — a hardcoded line range
        # silently starts truncating mid-sentence the first time it is edited.
        -h|--help) awk 'NR>1 && /^#/ {print; next} NR>1 {exit}' "$0"; exit 0 ;;
        all) HOSTS+=("${MIGRATED[@]}") ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) HOSTS+=("$arg") ;;
    esac
done
[[ ${#HOSTS[@]} -gt 0 ]] || { sed -n '2,7p' "$0" >&2; exit 2; }

say() { echo "==> $*"; }
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=5)
# What a deploy must never carry. .env holds per-host secrets and .secrets.env the
# GitHub PAT, which has no business on a deployed host at all; both survive on the
# host because they are also protected from --delete.
#
# Everything git ignores inside the host folder is excluded too, because deploy
# copies the WORKING TREE and not the git index: an ignored path that exists on
# disk gets pushed. bastion/vaultwarden/vw-data/ is the case that found this — a
# months-old copy of the Vaultwarden database and its JWT signing key, sitting in
# the working tree, which a deploy would have rsynced onto bastion and a backup
# then copied to vault. Those paths are gitignored precisely because they are live
# data, so "git ignores it" is exactly the right test.
#
# git's exit status is captured rather than piped, because a pipeline swallows it
# and so does the process substitution this used to be read through: if git failed
# for any reason, the function still returned 0 with nothing but the two static
# excludes, and the deploy would have pushed exactly the live data this exists to
# hold back. It fails the deploy instead.
host_excludes() {
    local host=$1 path ignored
    if ! ignored=$(git -C "$REPO_DIR" ls-files --others --ignored --exclude-standard \
                       --directory -- "$host/"); then
        echo "could not list ignored files under $host/; refusing to deploy it" >&2
        return 1
    fi
    printf '%s\n' --exclude=.env --exclude=.secrets.env
    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        printf -- '--exclude=/%s\n' "${path#"$host/"}"
    done <<<"$ignored"
}

WOKEN=()

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

# Wake a sleeping host and wait for it to accept SSH. The magic packet is layer 2,
# so this only works from a machine on the same LAN — true of every host here.
wake_host() {
    local host=$1 mac=${HOST_MAC[$host]:-} waited=0
    say "    $host is asleep; sending Wake-on-LAN to $mac"
    if command -v wakeonlan >/dev/null 2>&1; then
        wakeonlan "$mac" >/dev/null
    elif command -v etherwake >/dev/null 2>&1; then
        etherwake "$mac" >/dev/null
    else
        python3 - "$mac" <<'PY' >/dev/null || return 1
import socket, sys
mac = sys.argv[1].replace(":", "").replace("-", "")
packet = bytes.fromhex("ff" * 6 + mac * 16)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for dest in ("192.168.0.255", "255.255.255.255"):
    sock.sendto(packet, (dest, 9))
PY
    fi
    # Poll for SSH rather than sleeping a fixed amount: boot times vary, and SSH
    # (not ping) is what the deploy actually needs. apply.sh then waits for dockerd,
    # which comes up later still.
    while (( waited < WAKE_TIMEOUT )); do
        sleep 5; waited=$((waited + 5))
        if ssh "${SSH_OPTS[@]}" "${HOST_SSH[$host]}" true 2>/dev/null; then
            say "    $host answered SSH after ${waited}s"
            WOKEN+=("$host")
            return 0
        fi
    done
    return 1
}

deploy_host() {
    local host=$1
    local live="$DEST/$host" staging="$DEST/.staging/$host"
    local rsync_dest excludes=() exclude_str

    if [[ -z "${HOST_SSH[$host]:-}" ]]; then
        echo "unknown host: $host (known: ${!HOST_SSH[*]})" >&2; return 1
    fi
    if [[ ! " ${MIGRATED[*]} " == *" $host "* ]]; then
        echo "${NOT_MIGRATED[$host]:-$host is not migrated to deploy.sh yet; see README.}" >&2
        return 1
    fi
    if [[ $FORCE -eq 0 && $CHECK -eq 0 && -n "$(git -C "$REPO_DIR" status --porcelain -- "$host")" ]]; then
        echo "Refusing to deploy $host: uncommitted changes in $host/." >&2; return 1
    fi

    say "$host (${HOST_SSH[$host]}) @ $(git -C "$REPO_DIR" rev-parse --short HEAD)$([[ $CHECK -eq 1 ]] && echo ', check only')"
    if is_local "$host"; then
        rsync_dest="$staging/"
    else
        if ! ssh "${SSH_OPTS[@]}" "${HOST_SSH[$host]}" true 2>/dev/null; then
            if [[ -z "${HOST_MAC[$host]:-}" ]]; then
                echo "$host is unreachable and cannot be woken" >&2; return 1
            fi
            if [[ $WAKE -eq 0 ]]; then
                echo "$host is unreachable and --no-wake was given" >&2; return 1
            fi
            wake_host "$host" \
                || { echo "$host did not answer SSH within ${WAKE_TIMEOUT}s of Wake-on-LAN" >&2; return 1; }
        fi
        rsync_dest="${HOST_SSH[$host]}:$staging/"
    fi

    # Not `mapfile < <(...)`: a process substitution hides the exit status, which is
    # the whole point of host_excludes being able to fail.
    local exclude_list
    exclude_list=$(host_excludes "$host") || return 1
    mapfile -t excludes <<<"$exclude_list"
    exclude_str=$(printf '%q ' "${excludes[@]}")

    # set -e doesn't apply inside a function called from `||`, hence the explicit returns.
    on_host "$host" "mkdir -p '$staging' '$live'" || return 1
    rsync -a --delete "${excludes[@]}" -e "ssh ${SSH_OPTS[*]}" "$REPO_DIR/$host/" "$rsync_dest" || return 1

    # Scripts that must be identical on every host live in shared/ and are merged
    # into the host's scripts/ afterwards, so the --delete above cannot remove them.
    # One copy in the repo means they cannot drift apart -- which they had already
    # started to, with three copies of health-report.py -- while the paths on the
    # host are unchanged, so crontabs and authorized_keys do not care.
    if [[ -d "$REPO_DIR/shared" ]]; then
        rsync -a -e "ssh ${SSH_OPTS[*]}" "$REPO_DIR/shared/" "${rsync_dest%/}/scripts/" || return 1
    fi

    on_host "$host" "'$staging/apply.sh' --validate" || { echo "$host: validation failed, nothing applied" >&2; return 1; }

    echo "    files that would change in $live:"
    on_host "$host" "rsync -ani --delete $exclude_str '$staging/' '$live/' | sed 's/^/      /'" || return 1
    [[ $CHECK -eq 1 ]] && return 0

    on_host "$host" "rsync -a --delete $exclude_str '$staging/' '$live/' && '$live/apply.sh'" || return 1
}

failed=()
for host in "${HOSTS[@]}"; do
    deploy_host "$host" || failed+=("$host")
done
if [[ ${#WOKEN[@]} -gt 0 ]]; then
    say "Woken for this deploy and left running: ${WOKEN[*]}"
    say "  forge shuts down at 11pm via bastion's cron; sleep cinema with plex-power.py sleep"
fi
if [[ ${#failed[@]} -gt 0 ]]; then
    echo "FAILED: ${failed[*]}" >&2
    exit 1
fi
say "Done"
