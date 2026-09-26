#!/bin/bash
# sync-forge.sh — Sync backup data from forge (7700k) directly to tower (vault)
# Runs Sunday 3:00am, 15min after wake-forge.sh (see bastion/crontab); waits for forge to boot.
# Requires SSH key auth to both forge and tower:
#   ssh-copy-id robby@192.168.0.12
#   ssh-copy-id root@192.168.0.212

FORGE_IP="192.168.0.12"
FORGE_USER="robby"
TOWER_IP="192.168.0.212"
TOWER_USER="root"
SSH_KEY="/home/robby/.ssh/id_ed25519"
LOG="/home/robby/logs/backup.log"

# This path carries forge's openclaw-state/ — the agent's sessions, memory, identity
# and credentials — to root on vault. It used to pass StrictHostKeyChecking=no with
# UserKnownHostsFile=/dev/null, which accepts any host key presented, every time.
#
# accept-new pins each key on first contact and refuses a change afterwards, so no
# host has to be seeded by hand. After a legitimate rebuild, clear the entry:
#   ssh-keygen -R <ip>          (on bastion, and on forge for the tower entry)
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -i "$SSH_KEY")

mkdir -p "$(dirname "$LOG")"

# Wait for tower to be reachable
echo "$(date -Iseconds) [sync-forge] Waiting for tower to come online..." >> "$LOG"
TOWER_READY=0
for i in $(seq 1 12); do
    if ssh "${SSH_OPTS[@]}" "$TOWER_USER@$TOWER_IP" "echo ok" &>/dev/null; then
        TOWER_READY=1
        echo "$(date -Iseconds) [sync-forge] Tower is online" >> "$LOG"
        break
    fi
    echo "$(date -Iseconds) [sync-forge] Tower not ready, retry $i/12..." >> "$LOG"
    sleep 10
done
if [[ $TOWER_READY -eq 0 ]]; then
    echo "$(date -Iseconds) [sync-forge] ERROR: tower did not come online, aborting" >> "$LOG"
    exit 1
fi

# Wait for forge to be reachable (woken by wake-forge.sh 15min ago)
echo "$(date -Iseconds) [sync-forge] Waiting for forge to come online..." >> "$LOG"
FORGE_READY=0
for i in $(seq 1 24); do
    if ssh "${SSH_OPTS[@]}" "$FORGE_USER@$FORGE_IP" "echo ok" &>/dev/null; then
        FORGE_READY=1
        echo "$(date -Iseconds) [sync-forge] Forge is online" >> "$LOG"
        break
    fi
    echo "$(date -Iseconds) [sync-forge] Forge not ready, retry $i/24..." >> "$LOG"
    sleep 15
done
if [[ $FORGE_READY -eq 0 ]]; then
    echo "$(date -Iseconds) [sync-forge] ERROR: forge did not come online, aborting" >> "$LOG"
    exit 1
fi

echo "$(date -Iseconds) [sync-forge] Starting forge -> tower sync (forge pushes directly)..." >> "$LOG"

# SSH into forge and have it rsync directly to tower.
# Requires forge to have SSH key auth to tower set up:
#   On forge: ssh-copy-id root@192.168.0.212
SSH_CMD="ssh -o StrictHostKeyChecking=accept-new -i $SSH_KEY"
# Runs on forge, against forge's own ~/.ssh/known_hosts, not bastion's.
REMOTE_SSH="ssh -o StrictHostKeyChecking=accept-new"

# Documents/ covers the repo and everything developed on forge.
#
# --mkpath (rsync 3.2.3+) creates the whole destination path, not just its last
# component. Without it both of these transfers fail with
# `mkdir "/mnt/user/backups/forge/Documents" failed: No such file or directory`
# whenever the parent forge/ directory is missing — which is how the very first
# forge backup would always have failed, on a target nothing had provisioned.
$SSH_CMD "$FORGE_USER@$FORGE_IP" \
    "rsync -az --delete --mkpath \
        -e '$REMOTE_SSH' \
        /home/robby/Documents/ \
        $TOWER_USER@$TOWER_IP:/mnt/user/backups/forge/Documents/" >> "$LOG" 2>&1
RC1=$?

# The agent's live state deliberately lives outside the repo (see forge/apply.sh),
# so it is NOT covered by Documents/. Sessions, memory, workspace, identity and
# credentials exist nowhere else — without this they would silently stop being
# backed up the moment they moved out of the git tree.
$SSH_CMD "$FORGE_USER@$FORGE_IP" \
    "rsync -az --delete --mkpath \
        -e '$REMOTE_SSH' \
        /home/robby/openclaw-state/ \
        $TOWER_USER@$TOWER_IP:/mnt/user/backups/forge/openclaw-state/" >> "$LOG" 2>&1
RC2=$?

EXIT=$(( RC1 | RC2 ))

if [[ $EXIT -eq 0 ]]; then
    echo "$(date -Iseconds) [sync-forge] Forge -> tower sync complete" >> "$LOG"
else
    echo "$(date -Iseconds) [sync-forge] ERROR: rsync failed (documents=$RC1 openclaw-state=$RC2)" >> "$LOG"
    exit $EXIT
fi
