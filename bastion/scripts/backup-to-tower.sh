#!/bin/bash
# backup-to-tower.sh — Push all backup data from bastion (file) to tower (Unraid)
# Runs Sunday 2:30am, 30 minutes after wake-vault.sh.
#
# It does NOT assume wake-vault.sh succeeded. Cron runs the two independently, so a
# failed wake leaves this job running anyway; it checks the array itself before
# writing anything. See vault-common.sh.
#
# Requires SSH key auth to vault (root): ssh-copy-id -i ~/.ssh/id_ed25519.pub root@192.168.0.212

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/vault-common.sh"

TOWER_DEST="$VAULT_SSH:/mnt/user/backups"
LOG="/home/robby/logs/backup.log"
DATA_DIR="/home/robby/vaultwarden"
STAGING="/home/robby/backup-staging"

# Weekly point-in-time copies of vw-data, rotated. Separate from VW_BACKUPS, which
# apply.sh writes immediately before a version upgrade and which is never rotated —
# those mark known-risky moments and should outlive any schedule.
SNAPSHOT_DIR="/home/robby/vw-snapshots"
SNAPSHOT_KEEP=8
VW_BACKUPS="/home/robby/vw-backups"

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Iseconds) [backup-to-tower] $*" >> "$LOG"; }

# Wait for tower to be reachable. Until 2026-09-26 this loop fell through when the
# retries ran out and the script carried on regardless; sync-forge.sh grew a guard
# for that and this one never did.
log "Waiting for tower to come online..."
READY=0
for i in $(seq 1 20); do
    if vault_ssh "echo ok" &>/dev/null; then
        READY=1
        log "Tower is online"
        break
    fi
    log "Tower not ready, retry $i/20..."
    sleep 15
done
if [[ $READY -eq 0 ]]; then
    log "ERROR: tower never came online, aborting"
    exit 1
fi

# The gate. /mnt/user is RAM until the array starts; see vault-common.sh.
vault_require_array backup-to-tower "$LOG" || exit 1

log "Starting weekly backup to tower..."

EXIT=0

# Everything is staged locally first, then pushed. Two reasons, and the first is
# the one that matters:
#
#   * vw-data is a LIVE sqlite database. rsync of an open sqlite file can capture a
#     torn write or a state that does not match its WAL, so the copy on vault may
#     not open. Vaultwarden is stopped for the local copy only — seconds against a
#     local disk — and is running again long before the transfer to vault starts.
#   * AdGuard's agh-work/ and agh-conf/ are root-owned 0700/0600 because that
#     container runs as root, so robby's rsync cannot read them at all.
#
# Staging holds the vault database, its rsa_key.pem signing key, and
# AdGuardHome.yaml with the admin password hash. It stays 0700.
mkdir -p "$STAGING" && chmod 700 "$STAGING"

vw_stopped=0
restart_vaultwarden() {
    [[ $vw_stopped -eq 1 ]] || return 0
    if docker start vaultwarden >/dev/null 2>&1; then
        log "vaultwarden restarted"
    else
        log "ERROR: could not restart vaultwarden — the vault is DOWN"
    fi
    vw_stopped=0
}
# Covers every exit path, including an error between the stop and the start.
trap restart_vaultwarden EXIT

if docker stop vaultwarden >/dev/null 2>&1; then
    vw_stopped=1
    log "vaultwarden stopped for a consistent copy"
else
    log "ERROR: could not stop vaultwarden; copying it live, which may not restore"
    EXIT=1
fi

STAGED=0
if docker run --rm \
    -v "$DATA_DIR:/src:ro" \
    -v "$STAGING:/out" \
    alpine sh -c 'rm -rf /out/vaultwarden-data &&
                  mkdir -p /out/vaultwarden-data &&
                  cp -a /src/. /out/vaultwarden-data/ &&
                  chown -R '"$(id -u):$(id -g)"' /out/vaultwarden-data' >> "$LOG" 2>&1; then
    STAGED=1
else
    log "ERROR: could not stage bastion data"
    EXIT=1
fi

restart_vaultwarden

# --- a dated copy, not just a mirror -----------------------------------------------
# The push below is a MIRROR: --delete means last Sunday's copy is gone. Corruption,
# an accidental deletion, or anything else that damages vw-data therefore reaches
# vault on the next run with nothing older to fall back to. vw-data is a few MB, so a
# dated copy per week is almost free, and it is the difference between "the backup
# ran" and "I can get last month's vault back".
#
# Rotated here rather than on vault, so nothing ever runs rm over SSH against the one
# machine holding every backup; the mirror carries the rotation across. The limit of
# that is worth stating: a bastion that deletes its own snapshots propagates the
# deletion on the next run. Defending against that needs pull-based or append-only
# storage, which this does not attempt.
if [[ $STAGED -eq 1 ]]; then
    mkdir -p "$SNAPSHOT_DIR" && chmod 700 "$SNAPSHOT_DIR"
    snapshot="$SNAPSHOT_DIR/vw-data-$(date +%F).tgz"
    if tar czf "$snapshot" -C "$STAGING/vaultwarden-data" vw-data 2>> "$LOG"; then
        log "snapshot $(basename "$snapshot") ($(du -h "$snapshot" | cut -f1))"
        # Matches vw-data-YYYY-MM-DD.tgz and nothing else. VW_BACKUPS being a separate
        # directory is what really keeps apply.sh's pre-upgrade tarballs out of the
        # rotation, but a plain vw-data-*.tgz would also match their longer
        # vw-data-<date>-<time>-<version>.tgz names, so the date shape is spelled out
        # rather than left resting on the directory split alone.
        ls -1t "$SNAPSHOT_DIR"/vw-data-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].tgz \
            2>/dev/null | tail -n +$((SNAPSHOT_KEEP + 1)) \
            | while IFS= read -r old; do
                  rm -f -- "$old" && log "pruned $(basename "$old")"
              done
    else
        log "ERROR: could not write $snapshot"
        EXIT=1
    fi
fi

# Only a complete copy may be pushed: a partial one would --delete good data on tower.
if [[ $STAGED -eq 1 ]]; then
    rsync -az --delete --mkpath \
        -e "$VAULT_RSYNC_SSH" \
        "$STAGING/vaultwarden-data/" \
        "$TOWER_DEST/vaultwarden/" >> "$LOG" 2>&1 || EXIT=$?

    rsync -az --delete --mkpath \
        -e "$VAULT_RSYNC_SSH" \
        "$SNAPSHOT_DIR/" \
        "$TOWER_DEST/vaultwarden-snapshots/" >> "$LOG" 2>&1 || EXIT=$?
else
    log "ERROR: nothing was staged, so nothing was pushed"
fi

# apply.sh's pre-upgrade tarballs. No --delete: nothing prunes these, and a copy that
# exists only on bastion is not a backup — it is the single disk the whole point of
# this script is to get off.
if [[ -d "$VW_BACKUPS" ]]; then
    rsync -az --mkpath \
        -e "$VAULT_RSYNC_SSH" \
        "$VW_BACKUPS/" \
        "$TOWER_DEST/vaultwarden-pre-upgrade/" >> "$LOG" 2>&1 || EXIT=$?
fi

# Note: forge (7700k) data is synced directly to tower by sync-forge.sh at 3:00am

if [[ $EXIT -eq 0 ]]; then
    log "Weekly backup complete"
else
    log "ERROR: backup failed (exit $EXIT)"
    exit $EXIT
fi
