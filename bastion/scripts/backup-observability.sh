#!/bin/bash
# backup-observability.sh — nightly copies of the two stateful things in the observability
# stack that are not regenerable: the events database and Grafana's own database.
#
# Runs at 01:30 from bastion's crontab (before the Sunday 02:30 push to vault, so the newest
# copy is the one that travels). Prometheus and Loki data are deliberately not here: ninety
# days of metrics and thirty of logs are history, not state, and are the bulk of the disk.
#
#   ~/backups/events-YYYY-MM-DD.sql.gz    pg_dump of the events database
#   ~/backups/grafana-YYYY-MM-DD.tgz      the grafana_data volume (users, preferences; dashboards
#                                         and data sources are provisioned from git)
#   ~/backups/omada-YYYY-MM-DD.tgz        the Omada controller's own autobackup files
#   ~/backups/unifi-YYYY-MM-DD.tgz        the UniFi controller's own autobackup files
#
# Fourteen of each are kept; backup-to-tower.sh mirrors ~/backups/ to vault weekly.

set -uo pipefail
umask 077   # the archives hold password hashes and encrypted secrets; nobody else reads them

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/textfile-metrics.sh"

OUT="/home/robby/backups"
KEEP=14
LOG="/home/robby/logs/backup.log"
DAY=$(date +%F)

mkdir -p "$(dirname "$LOG")" "$OUT" && chmod 700 "$OUT"
log() { echo "$(date -Iseconds) [backup-observability] $*" >> "$LOG"; }

EXIT=0

if docker exec events-db pg_dump -U homelab -d homelab 2>>"$LOG" | gzip > "$OUT/events-$DAY.sql.gz.tmp" \
   && [[ -s "$OUT/events-$DAY.sql.gz.tmp" ]]; then
    mv -f "$OUT/events-$DAY.sql.gz.tmp" "$OUT/events-$DAY.sql.gz"
    log "events database dumped ($(du -h "$OUT/events-$DAY.sql.gz" | cut -f1))"
else
    rm -f "$OUT/events-$DAY.sql.gz.tmp"
    log "ERROR: pg_dump of the events database failed"
    EXIT=1
fi

# jobhunt's database (bastion/jobhunt): postings, scores and the application pipeline. A
# stopped or absent container is skipped quietly, so this line costs nothing until it exists.
if docker inspect jobhunt-db >/dev/null 2>&1; then
    if docker exec jobhunt-db pg_dump -U jobhunt -d jobhunt 2>>"$LOG" | gzip > "$OUT/jobhunt-$DAY.sql.gz.tmp" \
       && [[ -s "$OUT/jobhunt-$DAY.sql.gz.tmp" ]]; then
        mv -f "$OUT/jobhunt-$DAY.sql.gz.tmp" "$OUT/jobhunt-$DAY.sql.gz"
        log "jobhunt database dumped ($(du -h "$OUT/jobhunt-$DAY.sql.gz" | cut -f1))"
    else
        rm -f "$OUT/jobhunt-$DAY.sql.gz.tmp"
        log "ERROR: pg_dump of the jobhunt database failed"
        EXIT=1
    fi
fi

# The volume is read through a throwaway container, so nothing on the host needs to know
# where Docker keeps it. Grafana keeps running; its SQLite is small and written rarely.
# plugins/ is skipped: it is the bundled plugin cache (100+ MB) and comes back with the image.
# tar runs as root (the volume's files are not world-readable), so the archive is handed back
# to robby and closed to everyone else before the container exits.
if docker run --rm -v observability_grafana_data:/d:ro -v "$OUT:/out" alpine sh -c \
        "tar czf /out/grafana-$DAY.tgz --exclude=./plugins -C /d . && chown $(id -u):$(id -g) /out/grafana-$DAY.tgz && chmod 600 /out/grafana-$DAY.tgz" 2>>"$LOG"; then
    log "grafana volume archived ($(du -h "$OUT/grafana-$DAY.tgz" | cut -f1))"
else
    log "ERROR: could not archive the grafana volume"
    EXIT=1
fi

# The WiFi controllers (bastion/wifi) each write their own nightly autobackup into their
# volume; those files are the restorable form (the UIs import them), so they are what travels.
# A controller that is not running yet, or has no autobackup yet, is skipped without an
# error: the directory does not exist until the first-run wizard has been through.
for spec in "omada:wifi_omada_data:autobackup" "unifi:wifi_unifi_config:data/backup/autobackup"; do
    IFS=: read -r name volume sub <<<"$spec"
    docker volume inspect "$volume" >/dev/null 2>&1 || continue
    if ! docker run --rm -v "$volume:/d:ro" alpine test -d "/d/$sub" 2>/dev/null; then
        log "$name: no autobackup directory yet, skipped"
        continue
    fi
    if docker run --rm -v "$volume:/d:ro" -v "$OUT:/out" alpine sh -c \
            "tar czf /out/$name-$DAY.tgz -C /d/$sub . && chown $(id -u):$(id -g) /out/$name-$DAY.tgz && chmod 600 /out/$name-$DAY.tgz" 2>>"$LOG"; then
        log "$name autobackups archived ($(du -h "$OUT/$name-$DAY.tgz" | cut -f1))"
    else
        log "ERROR: could not archive the $name autobackups"
        EXIT=1
    fi
done

for pattern in "events-*.sql.gz" "jobhunt-*.sql.gz" "grafana-*.tgz" "omada-*.tgz" "unifi-*.tgz"; do
    ls -1t "$OUT"/$pattern 2>/dev/null | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
        rm -f -- "$old" && log "pruned $(basename "$old")"
    done
done

record_job backup-observability "$EXIT"
"$HERE/event.sh" backup backup-observability "$([[ $EXIT -eq 0 ]] && echo ok || echo error)"
exit $EXIT
