#!/bin/bash
# sync-forge.sh — Sync backup data from forge (7700k) directly to tower (vault)
# Runs at 10pm daily (before sleep-forge.sh at 11pm).
# Requires SSH key auth to both forge and tower:
#   ssh-copy-id robby@192.168.0.13
#   ssh-copy-id robby@192.168.0.212

FORGE_IP="192.168.0.12"
FORGE_USER="robby"
TOWER_IP="192.168.0.212"
TOWER_USER="root"
SSH_KEY="/home/robby/.ssh/id_ed25519"
LOG="/home/robby/logs/backup.log"

mkdir -p "$(dirname "$LOG")"

# Wait for tower to be reachable
echo "$(date -Iseconds) [sync-forge] Waiting for tower to come online..." >> "$LOG"
TOWER_READY=0
for i in $(seq 1 12); do
    if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
           -o ConnectTimeout=5 -i "$SSH_KEY" \
           "$TOWER_USER@$TOWER_IP" "echo ok" &>/dev/null; then
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
    if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
           -o ConnectTimeout=5 -i "$SSH_KEY" \
           "$FORGE_USER@$FORGE_IP" "echo ok" &>/dev/null; then
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
#   On forge: ssh-copy-id robby@192.168.0.212
SSH_CMD="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i $SSH_KEY"
REMOTE_SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Documents/ covers everything that used to be synced separately: forge's compose
# stack and openclaw state now live in Documents/Github/Homelab/forge/.
$SSH_CMD "$FORGE_USER@$FORGE_IP" \
    "rsync -az --delete \
        -e '$REMOTE_SSH' \
        /home/robby/Documents/ \
        $TOWER_USER@$TOWER_IP:/mnt/user/backups/forge/Documents/" >> "$LOG" 2>&1
EXIT=$?

if [[ $EXIT -eq 0 ]]; then
    echo "$(date -Iseconds) [sync-forge] Forge -> tower sync complete" >> "$LOG"
else
    echo "$(date -Iseconds) [sync-forge] ERROR: rsync failed (rc=$EXIT)" >> "$LOG"
    exit $EXIT
fi
