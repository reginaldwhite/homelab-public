#!/bin/bash
# sleep-forge.sh — Gracefully shut down forge (7700k) via SSH
# Runs on bastion (file). Requires SSH key auth: ssh-copy-id robby@192.168.0.12
# Needs passwordless sudo for shutdown on forge.

FORGE_IP="192.168.0.12"
FORGE_USER="robby"
SSH_KEY="/home/robby/.ssh/id_ed25519"
LOG="/home/robby/logs/power.log"

mkdir -p "$(dirname "$LOG")"

echo "$(date -Iseconds) [sleep-forge] Sending shutdown to forge ($FORGE_IP)..." >> "$LOG"

ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=10 \
    -i "$SSH_KEY" \
    "$FORGE_USER@$FORGE_IP" \
    "sudo shutdown -h now" >> "$LOG" 2>&1

EXIT=$?
if [[ $EXIT -eq 0 || $EXIT -eq 255 ]]; then
    # 255 = SSH disconnected immediately because host shut down — that's expected
    echo "$(date -Iseconds) [sleep-forge] Shutdown command accepted by forge" >> "$LOG"
else
    echo "$(date -Iseconds) [sleep-forge] ERROR: SSH to forge failed (exit $EXIT)" >> "$LOG"
    exit $EXIT
fi
