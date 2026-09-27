#!/bin/bash
# textfile-metrics.sh — let bastion's scripts publish facts to Prometheus. Sourced, never run.
#
#   . "$HERE/textfile-metrics.sh"
#   record_job backup-to-tower "$EXIT"      # last run, last exit, last success timestamps
#   set_expected_up forge 1                 # bastion asked forge to be up (0 = asked it to sleep)
#
# node exporter on this host runs with --collector.textfile.directory pointing at $TEXTFILE_DIR
# (see ../observability/docker-compose.yml). Every .prom file there is read on each scrape, so a
# script only has to leave the current truth in a file; there is no daemon and nothing to
# connect to. Files are written to a temp name and renamed, so a scrape never reads a half file.
#
# Why these two facts, and nothing cleverer:
#   * homelab_job_last_success_timestamp is what "the backup is late" is computed from; it
#     survives a failed run (a failure updates last_run and last_exit but leaves last_success
#     where it was), which is exactly how the old health verb read the log by hand.
#   * homelab_host_expected_up is how alerting copes with hosts that sleep on purpose. The
#     scripts that decide when forge and vault should be up are the ones that set it, so it
#     records intent, survives daylight-saving changes, and needs no clock arithmetic in a rule.
#
# On a host without the directory (forge, cinema, or bastion before phase 1) every function is
# a no-op, so the scripts that call these do not care where they run.

TEXTFILE_DIR="${TEXTFILE_DIR:-/home/robby/observability/textfile}"

# Write stdin to $TEXTFILE_DIR/<stem>.prom atomically. No-op when the directory is absent.
metric_write() {
    local stem=$1 f
    [[ -d "$TEXTFILE_DIR" ]] || return 0
    f="$TEXTFILE_DIR/$stem.prom"
    cat > "$f.tmp" 2>/dev/null && mv -f "$f.tmp" "$f" 2>/dev/null || true
}

# record_job <job> <exit-code>
record_job() {
    local job=$1 rc=$2 now prev f
    now=$(date +%s)
    f="$TEXTFILE_DIR/job-$job.prom"
    prev=""
    [[ -f "$f" ]] && prev=$(awk '/^homelab_job_last_success_timestamp/ {print $2}' "$f" 2>/dev/null)
    local success=${prev:-0}
    [[ "$rc" == 0 ]] && success=$now
    printf 'homelab_job_last_run_timestamp{job="%s"} %s\nhomelab_job_last_exit{job="%s"} %s\nhomelab_job_last_success_timestamp{job="%s"} %s\n' \
        "$job" "$now" "$job" "$rc" "$job" "$success" | metric_write "job-$job"
}

# set_expected_up <host> <0|1>
set_expected_up() {
    printf 'homelab_host_expected_up{host="%s"} %s\n' "$1" "$2" | metric_write "expected-up-$1"
}
