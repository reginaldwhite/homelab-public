#!/bin/bash
# backup-to-tower.sh — Push all backup data from bastion (file) to tower (Unraid)
# Runs Sunday 2:30am — 30 minutes after wake-vault.sh wakes tower and waits for its
# array to mount, so /mnt/user is a real filesystem and not Unraid's RAM root.
# Requires SSH key auth to vault (root): ssh-copy-id -i ~/.ssh/id_ed25519.pub root@192.168.0.212

TOWER_IP="192.168.0.212"
TOWER_USER="root"
SSH_KEY="/home/robby/.ssh/id_ed25519"
TOWER_DEST="$TOWER_USER@$TOWER_IP:/mnt/user/backups"
LOG="/home/robby/logs/backup.log"

# This connection carries vw-data — the Vaultwarden sqlite DB and rsa_key.pem, its
# JWT signing key — plus AdGuardHome.yaml with the admin password hash, to root on
# vault. It used to pass StrictHostKeyChecking=no with UserKnownHostsFile=/dev/null,
# which accepts a different host key every single week: anyone able to ARP-spoof
# 192.168.0.212 during the Sunday window would have been handed the vault.
#
# accept-new rather than yes: it pins vault's key on first contact and refuses any
# change afterwards, without needing every host seeded by hand first. If vault is
# ever rebuilt its key changes and this will refuse to connect — that is the point.
# Clear the stale entry deliberately:
#   ssh-keygen -R 192.168.0.212
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -i "$SSH_KEY")

mkdir -p "$(dirname "$LOG")"

# Wait for tower to be reachable (plug just came on, give it time to boot)
echo "$(date -Iseconds) [backup-to-tower] Waiting for tower to come online..." >> "$LOG"
for i in $(seq 1 20); do
    if ssh "${SSH_OPTS[@]}" "$TOWER_USER@$TOWER_IP" "echo ok" &>/dev/null; then
        echo "$(date -Iseconds) [backup-to-tower] Tower is online" >> "$LOG"
        break
    fi
    echo "$(date -Iseconds) [backup-to-tower] Tower not ready, retry $i/20..." >> "$LOG"
    sleep 15
done

echo "$(date -Iseconds) [backup-to-tower] Starting weekly backup to tower..." >> "$LOG"

RSYNC_SSH="ssh -o StrictHostKeyChecking=accept-new -i $SSH_KEY"

DATA_DIR="/home/robby/vaultwarden"
STAGING="/home/robby/backup-staging"
EXIT=0

# AdGuard's agh-work/ and agh-conf/ are root-owned (0700/0600) because the
# container runs as root, so robby's rsync can't read them. Copy them out through
# a throwaway container, which runs as root, into a robby-owned staging dir.
# Staging holds AdGuardHome.yaml (admin password hash), so keep it private.
mkdir -p "$STAGING" && chmod 700 "$STAGING"
STAGED=0
if docker run --rm \
    -v "$DATA_DIR:/src:ro" \
    -v "$STAGING:/out" \
    alpine sh -c 'rm -rf /out/agh-work /out/agh-conf &&
                  cp -a /src/agh-work /src/agh-conf /out/ &&
                  chown -R '"$(id -u):$(id -g)"' /out/agh-work /out/agh-conf' >> "$LOG" 2>&1; then
    STAGED=1
else
    echo "$(date -Iseconds) [backup-to-tower] ERROR: could not stage AdGuard data" >> "$LOG"
    EXIT=1
fi

# Vaultwarden data and the rest of ~/vaultwarden. The AdGuard dirs are excluded
# here (and so also protected from --delete) and sent from staging below.
rsync -az --delete --mkpath \
    --exclude=/agh-work/ --exclude=/agh-conf/ \
    -e "$RSYNC_SSH" \
    "$DATA_DIR/" \
    "$TOWER_DEST/vaultwarden/" >> "$LOG" 2>&1 || EXIT=$?

# Only a complete copy may be pushed: a partial one would --delete good data on tower.
if [[ $STAGED -eq 1 ]]; then
    rsync -az --delete --mkpath \
        -e "$RSYNC_SSH" \
        "$STAGING/agh-work" "$STAGING/agh-conf" \
        "$TOWER_DEST/vaultwarden/" >> "$LOG" 2>&1 || EXIT=$?
fi

# Note: forge (7700k) data is synced directly to tower by sync-forge.sh at 3:00am

if [[ $EXIT -eq 0 ]]; then
    echo "$(date -Iseconds) [backup-to-tower] Weekly backup complete" >> "$LOG"
else
    echo "$(date -Iseconds) [backup-to-tower] ERROR: One or more rsyncs failed (exit $EXIT)" >> "$LOG"
    exit $EXIT
fi
