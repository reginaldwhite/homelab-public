#!/bin/bash
# sleep-forge.sh — Gracefully shut down forge (7700k) via SSH, then confirm it goes down
# Runs on bastion (file). Requires SSH key auth: ssh-copy-id robby@192.168.0.12
# Needs passwordless sudo for shutdown on forge.

FORGE_IP="192.168.0.12"
FORGE_USER="robby"
SSH_KEY="/home/robby/.ssh/id_ed25519"
DOWN_TIMEOUT=120   # seconds for forge to stop answering ping after shutdown
LOG="/home/robby/logs/power.log"

mkdir -p "$(dirname "$LOG")"

forge_up() { ping -c1 -W2 "$FORGE_IP" &>/dev/null; }

# ssh exits 255 both when forge drops the connection mid-shutdown and when it was never
# reachable, so check first rather than read the exit code.
if ! forge_up; then
    echo "$(date -Iseconds) [sleep-forge] Forge already down (no ping reply), nothing to do" >> "$LOG"
    exit 0
fi

echo "$(date -Iseconds) [sleep-forge] Sending shutdown to forge ($FORGE_IP)..." >> "$LOG"

ssh -o StrictHostKeyChecking=accept-new \
    -o LogLevel=ERROR \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    -i "$SSH_KEY" \
    "$FORGE_USER@$FORGE_IP" \
    "sudo -n shutdown -h now" >> "$LOG" 2>&1

SECONDS=0
while (( SECONDS < DOWN_TIMEOUT )); do
    if ! forge_up; then
        echo "$(date -Iseconds) [sleep-forge] Forge is down (${SECONDS}s)" >> "$LOG"
        exit 0
    fi
    sleep 5
done
echo "$(date -Iseconds) [sleep-forge] ERROR: forge still answering ping ${DOWN_TIMEOUT}s after shutdown" >> "$LOG"
exit 1
