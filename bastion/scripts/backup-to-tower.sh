#!/bin/bash
# backup-to-tower.sh — Push all backup data from bastion (file) to tower (Unraid)
# Runs Sunday 2:30am — 30 minutes after smart plug powers tower on.
# Requires SSH key auth to vault (root): ssh-copy-id -i ~/.ssh/id_ed25519.pub root@192.168.0.212

TOWER_IP="192.168.0.212"
TOWER_USER="root"
SSH_KEY="/home/robby/.ssh/id_ed25519"
TOWER_DEST="$TOWER_USER@$TOWER_IP:/mnt/user/backups"
LOG="/home/robby/logs/backup.log"

mkdir -p "$(dirname "$LOG")"

# Wait for tower to be reachable (plug just came on, give it time to boot)
echo "$(date -Iseconds) [backup-to-tower] Waiting for tower to come online..." >> "$LOG"
for i in $(seq 1 20); do
    if ssh -o StrictHostKeyChecking=no \
           -o UserKnownHostsFile=/dev/null \
           -o ConnectTimeout=5 \
           -i "$SSH_KEY" \
           "$TOWER_USER@$TOWER_IP" "echo ok" &>/dev/null; then
        echo "$(date -Iseconds) [backup-to-tower] Tower is online" >> "$LOG"
        break
    fi
    echo "$(date -Iseconds) [backup-to-tower] Tower not ready, retry $i/20..." >> "$LOG"
    sleep 15
done

echo "$(date -Iseconds) [backup-to-tower] Starting weekly backup to tower..." >> "$LOG"

SSH_OPTS="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i $SSH_KEY"

# Vaultwarden + AdGuard config (local on bastion)
rsync -az --delete \
    -e "$SSH_OPTS" \
    /home/robby/vaultwarden/ \
    "$TOWER_DEST/vaultwarden/" >> "$LOG" 2>&1

# Note: forge (7700k) data is synced directly to tower by sync-forge.sh at 3:00am

EXIT=$?
if [[ $EXIT -eq 0 ]]; then
    echo "$(date -Iseconds) [backup-to-tower] Weekly backup complete" >> "$LOG"
else
    echo "$(date -Iseconds) [backup-to-tower] ERROR: One or more rsyncs failed (exit $EXIT)" >> "$LOG"
    exit $EXIT
fi
