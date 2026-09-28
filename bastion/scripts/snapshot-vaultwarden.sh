#!/bin/bash
# snapshot-vaultwarden.sh — a dated copy of the vault's data every night, so the recovery point
# is a day rather than a week.
#
# Runs at 01:15 from bastion's crontab, ahead of backup-observability.sh (01:30) and the Sunday
# 02:30 push, which mirrors ~/vw-snapshots/ to vault. This is the stop-copy-tar-restart part of
# backup-to-tower.sh and nothing else: the container is stopped only for the local copy (a few
# seconds against a local disk), because rsync or tar of an open SQLite file can capture a torn
# write that does not restore.
#
#   ~/vw-snapshots/vw-data-YYYY-MM-DD.tgz     db.sqlite3, rsa_key.pem, attachments, icon cache
#
# Fourteen are kept, matching the 14-day rotation of the observability copies. Sunday's run of
# backup-to-tower.sh writes the same file name for that day and simply overwrites it.

set -uo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/textfile-metrics.sh"

DATA_DIR="/home/robby/vaultwarden/vw-data"
SNAPSHOT_DIR="/home/robby/vw-snapshots"
SNAPSHOT_KEEP=14
LOG="/home/robby/logs/backup.log"
DAY=$(date +%F)
TMP=$(mktemp -d /home/robby/.vw-snapshot.XXXXXX)

mkdir -p "$(dirname "$LOG")" "$SNAPSHOT_DIR" && chmod 700 "$SNAPSHOT_DIR"
log() { echo "$(date -Iseconds) [snapshot-vaultwarden] $*" >> "$LOG"; }

EXIT=0
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
cleanup() { restart_vaultwarden; rm -rf "$TMP"; }
trap cleanup EXIT

if docker stop vaultwarden >/dev/null 2>&1; then
    vw_stopped=1
else
    log "ERROR: could not stop vaultwarden; copying it live, which may not restore"
    EXIT=1
fi

# vw-data is root-owned (the container runs as root), so the copy is made by a throwaway
# container and handed to robby, exactly as backup-to-tower.sh does it.
if docker run --rm -v "$DATA_DIR:/src:ro" -v "$TMP:/out" alpine sh -c \
        'mkdir -p /out/vw-data && cp -a /src/. /out/vw-data/ && chown -R '"$(id -u):$(id -g)"' /out/vw-data' >> "$LOG" 2>&1; then
    COPIED=1
else
    log "ERROR: could not copy vw-data"
    COPIED=0
    EXIT=1
fi
restart_vaultwarden

if [[ $COPIED -eq 1 ]]; then
    snapshot="$SNAPSHOT_DIR/vw-data-$DAY.tgz"
    if tar czf "$snapshot.tmp" -C "$TMP" vw-data 2>> "$LOG" && mv -f "$snapshot.tmp" "$snapshot"; then
        log "snapshot $(basename "$snapshot") ($(du -h "$snapshot" | cut -f1))"
        ls -1t "$SNAPSHOT_DIR"/vw-data-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].tgz 2>/dev/null \
            | tail -n +$((SNAPSHOT_KEEP + 1)) | while IFS= read -r old; do
                  rm -f -- "$old" && log "pruned $(basename "$old")"
              done
    else
        rm -f "$snapshot.tmp"
        log "ERROR: could not write $snapshot"
        EXIT=1
    fi
fi

record_job snapshot-vaultwarden "$EXIT"
"$HERE/event.sh" backup snapshot-vaultwarden "$([[ $EXIT -eq 0 ]] && echo ok || echo error)"
exit $EXIT
