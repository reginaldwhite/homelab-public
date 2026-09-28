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

for pattern in "events-*.sql.gz" "grafana-*.tgz"; do
    ls -1t "$OUT"/$pattern 2>/dev/null | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
        rm -f -- "$old" && log "pruned $(basename "$old")"
    done
done

record_job backup-observability "$EXIT"
"$HERE/event.sh" backup backup-observability "$([[ $EXIT -eq 0 ]] && echo ok || echo error)"
exit $EXIT
