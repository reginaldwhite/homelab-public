#!/bin/bash
# event.sh — record one row in the events database on bastion.
#
#   event.sh <kind> <name> <status> [--host H] [--started ISO8601] [--detail JSON] [--source-id ID]
#
#   event.sh backup backup-to-tower ok --detail '{"seconds": 6}'
#   event.sh power wake-vault error --detail '{"reason": "no ping within 300s"}'
#   event.sh sauna session ok --started 2026-09-27T15:48:00-05:00 --detail '{"minutes": 64, "peak_c": 84}'
#
# Goes through `docker exec … psql` so nothing on the host needs a database driver, and every
# value travels as a psql variable, never spliced into SQL text -- a hostname with a quote in
# it is data, not a statement. --source-id makes an insert idempotent (unique when present),
# which is how the sprinkler poller can re-read a day of upstream events without duplicates.
#
# On a host without the database container this is a silent no-op, so the shared scripts can
# call it everywhere. Failures to write are logged, never fatal: an event store that could
# abort a backup would be worse than no event store.

set -uo pipefail

DB_CONTAINER="events-db"
LOG="/home/robby/logs/events.log"

usage() { sed -n '2,9p' "$0" >&2; exit 2; }
[[ $# -ge 3 ]] || usage
kind=$1 name=$2 status=$3; shift 3

case "$(hostname)" in
    file)       host=bastion ;;
    7700k)      host=forge ;;
    plex-7600t) host=cinema ;;
    *)          host=$(hostname) ;;
esac
started="" detail="" source_id=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)      host=$2; shift 2 ;;
        --started)   started=$2; shift 2 ;;
        --detail)    detail=$2; shift 2 ;;
        --source-id) source_id=$2; shift 2 ;;
        *) usage ;;
    esac
done

docker inspect "$DB_CONTAINER" >/dev/null 2>&1 || exit 0

if ! out=$(docker exec -i "$DB_CONTAINER" psql -q -U homelab -d homelab -v ON_ERROR_STOP=1 \
        -v k="$kind" -v n="$name" -v s="$status" -v h="$host" -v st="$started" -v d="$detail" -v sid="$source_id" \
        2>&1 <<'SQL'
insert into events (ts, started_at, host, kind, name, status, detail, source_id)
values (now(), nullif(:'st', '')::timestamptz, :'h', :'k', :'n', :'s', nullif(:'d', '')::jsonb, nullif(:'sid', ''))
on conflict (source_id) where source_id is not null do nothing;
SQL
); then
    mkdir -p "$(dirname "$LOG")"
    echo "$(date -Iseconds) [event] ERROR: could not record $kind/$name/$status: ${out:0:200}" >> "$LOG"
    exit 1
fi
