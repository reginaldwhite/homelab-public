#!/bin/bash
# apply.sh — Apply bastion's config. Run by ../deploy.sh on bastion, from the
# deployed copy (/home/robby/homelab-deployed/bastion), never from a git clone.
#
#   apply.sh --validate   # check this copy; changes nothing (run from staging)
#   apply.sh              # install crontab, compose up, reload caddy
#
# Cron runs the scripts straight from the deployed copy (see crontab), so there
# is nothing to copy for them.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$HERE/vaultwarden"
# The observability stack (docs/plan-observability.md): its own compose project, joining the
# vaultwarden network. Its variables come from this host's .env, which lives beside apply.sh
# in the deployed copy and is absent from staging -- so validation tolerates a missing file
# (compose then warns about unset variables, which is fine) and apply insists on it.
OBS_DIR="$HERE/observability"
OBS_ENV="$HERE/.env"
# The WiFi controllers (docs/plan-wifi.md): third compose project, host network, same .env.
WIFI_DIR="$HERE/wifi"
# Where bastion's scripts drop .prom files for node exporter's textfile collector.
TEXTFILE_DIR="/home/robby/observability/textfile"
# Hashes of the config trees as last applied, so a changed bind-mounted config reloads its service.
OBS_APPLIED="/home/robby/observability/.applied"
VW_DATA="/home/robby/vaultwarden/vw-data"
VW_BACKUPS="/home/robby/vw-backups"

VALIDATE=0
[[ "${1:-}" == "--validate" ]] && VALIDATE=1

OPENCLAW_CMD="/home/robby/bin/openclaw-cmd"

say() { echo "    $*"; }

cd "$STACK_DIR"
want_vw=$(docker compose config --images | grep '^vaultwarden/' | head -1)
have_vw=$(docker inspect vaultwarden --format '{{.Config.Image}}' 2>/dev/null || true)

# A just-woken host accepts SSH before dockerd is ready, and --validate runs
# immediately after the wake. Wait here too, not only in the apply phase, or a
# deploy to a sleeping host fails validation on a race rather than on a real fault.
wait_for_docker() {
    for _ in $(seq 1 30); do
        docker info >/dev/null 2>&1 && return 0
        sleep 2
    done
    echo "docker never became ready" >&2
    return 1
}

# --- validate ----------------------------------------------------------------------
if [[ $VALIDATE -eq 1 ]]; then
    wait_for_docker || exit 1
    docker compose config --quiet
    caddy_image=$(docker compose config --images | grep '^caddy' | head -1)
    if ! out=$(docker run --rm -v "$STACK_DIR/caddy:/etc/caddy:ro" "$caddy_image" \
            caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1); then
        echo "Caddyfile failed validation:" >&2
        echo "$out" >&2
        exit 1
    fi
    for f in "$HERE"/scripts/*.sh;  do [[ -e "$f" ]] && bash -n "$f"; done
    for f in "$HERE"/scripts/*.py;  do [[ -e "$f" ]] && python3 -m py_compile "$f"; done
    rm -rf "$HERE/scripts/__pycache__"
    # openclaw-cmd has no extension, so the globs above miss it, and it is the one
    # script whose breakage locks the agent out rather than just failing a job.
    if [[ -e "$HERE/scripts/openclaw-cmd" ]]; then bash -n "$HERE/scripts/openclaw-cmd"; fi
    say "compose file, Caddyfile and scripts valid"

    # Observability: compose config, then each config file against the exact image that will
    # read it, so a typo in prometheus.yml or blackbox.yml stops the deploy here rather than
    # crash-looping a container on the vault host.
    if [[ -d "$OBS_DIR" ]]; then
        obs_env=(); [[ -f "$OBS_ENV" ]] && obs_env=(--env-file "$OBS_ENV")
        docker compose "${obs_env[@]}" -f "$OBS_DIR/docker-compose.yml" config --quiet
        prom_image=$(docker compose "${obs_env[@]}" -f "$OBS_DIR/docker-compose.yml" config --images | grep '^prom/prometheus' | head -1)
        bb_image=$(docker compose "${obs_env[@]}" -f "$OBS_DIR/docker-compose.yml" config --images | grep '^prom/blackbox' | head -1)
        if ! out=$(docker run --rm -v "$OBS_DIR/prometheus:/etc/prometheus:ro" --entrypoint promtool \
                "$prom_image" check config /etc/prometheus/prometheus.yml 2>&1); then
            echo "prometheus.yml failed validation:" >&2; echo "$out" >&2; exit 1
        fi
        if ! out=$(docker run --rm -v "$OBS_DIR/blackbox:/etc/blackbox:ro" \
                "$bb_image" --config.file=/etc/blackbox/blackbox.yml --config.check 2>&1); then
            echo "blackbox.yml failed validation:" >&2; echo "$out" >&2; exit 1
        fi
        python3 -c "import json,sys; [json.load(open(f)) for f in sys.argv[1:]]" \
            "$OBS_DIR"/grafana/dashboards/*.json
        loki_image=$(docker compose "${obs_env[@]}" -f "$OBS_DIR/docker-compose.yml" config --images | grep '^grafana/loki' | head -1)
        alloy_image=$(docker compose "${obs_env[@]}" -f "$OBS_DIR/docker-compose.yml" config --images | grep '^grafana/alloy' | head -1)
        if ! out=$(docker run --rm -v "$OBS_DIR/loki:/etc/loki:ro" "$loki_image" -config.file=/etc/loki/loki.yml -verify-config 2>&1); then
            echo "loki.yml failed validation:" >&2; echo "$out" >&2; exit 1
        fi
        # The shipper config is the shared one every host mounts; dummy values stand in for
        # the environment it reads at run time.
        if ! out=$(docker run --rm -e LOKI_URL=http://loki:3100/loki/api/v1/push -e LOKI_USER=x -e LOKI_PASSWORD=x -e ALLOY_HOST=x \
                -v "$HERE/scripts/alloy/config.alloy:/etc/alloy/config.alloy:ro" "$alloy_image" validate /etc/alloy/config.alloy 2>&1); then
            echo "config.alloy failed validation:" >&2; echo "$out" >&2; exit 1
        fi
        say "observability: compose, prometheus.yml, blackbox.yml, loki.yml, config.alloy and $(ls "$OBS_DIR"/grafana/dashboards/*.json | wc -l | tr -d ' ') dashboards valid"
    fi
    if [[ -d "$WIFI_DIR" ]]; then
        wifi_env=(); [[ -f "$OBS_ENV" ]] && wifi_env=(--env-file "$OBS_ENV")
        docker compose "${wifi_env[@]}" -f "$WIFI_DIR/docker-compose.yml" config --quiet
        bash -n "$WIFI_DIR/unifi/init-mongo.sh"
        say "wifi: compose file and init-mongo.sh valid"
    fi
    [[ -n "$have_vw" && "$have_vw" != "$want_vw" ]] \
        && say "vaultwarden: $have_vw -> $want_vw (vw-data is backed up first)"
    crontab -l 2>/dev/null | diff -u --label installed --label repo - "$HERE/crontab" \
        | sed 's/^/    /' || true
    exit 0
fi

# --- apply -------------------------------------------------------------------------
crontab "$HERE/crontab"
say "crontab installed"

# The agent's forced command in ~/.ssh/authorized_keys points at ~/bin/openclaw-cmd,
# not at the deployed copy, so a host that has never been deployed to still has a
# working health verb rather than an SSH key that fails closed. Deploy keeps it in
# sync; before this, it was hand-copied to three hosts and drifted.
if ! cmp -s "$HERE/scripts/openclaw-cmd" "$OPENCLAW_CMD"; then
    install -D -m 755 "$HERE/scripts/openclaw-cmd" "$OPENCLAW_CMD"
    say "openclaw-cmd updated at $OPENCLAW_CMD"
fi

# Vaultwarden's DB migrations are one-way, so a version change gets a backup first.
if [[ -n "$have_vw" && "$have_vw" != "$want_vw" ]]; then
    say "Vaultwarden $have_vw -> $want_vw: backing up vw-data first"
    docker compose pull vaultwarden
    docker compose stop vaultwarden
    mkdir -p "$VW_BACKUPS"
    backup="vw-data-$(date +%F-%H%M%S)-${have_vw##*:}.tgz"
    docker run --rm -v "$VW_DATA:/d:ro" -v "$VW_BACKUPS:/out" \
        alpine tar czf "/out/$backup" -C /d .
    say "$VW_BACKUPS/$backup"
fi

docker compose up -d

# `up` leaves caddy running when only the Caddyfile's contents changed. A reload
# applies it without dropping connections, and is a no-op when nothing changed.
for i in $(seq 1 10); do
    # If `up` just recreated caddy, its admin API may take a moment to come up.
    docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null && break
    [[ $i -eq 10 ]] && { echo "caddy reload failed" >&2; exit 1; }
    sleep 2
done
say "caddy config reloaded"

docker compose ps --format '    {{.Name}}\t{{.Image}}\t{{.Status}}'
[[ "$have_vw" != "$want_vw" ]] && docker logs vaultwarden 2>&1 | grep -m1 -i 'version' || true

# --- observability ----------------------------------------------------------------------
if [[ -d "$OBS_DIR" ]]; then
    for var in GRAFANA_ADMIN_PASSWORD GRAFANA_SECRET_KEY EVENTS_DB_PASSWORD SLACK_BOT_TOKEN; do
        if [[ ! -f "$OBS_ENV" ]] || ! grep -qE "^$var=.+" "$OBS_ENV"; then
            echo "observability: $var is not set in $OBS_ENV; not starting the stack" >&2
            echo "  (add it -- see .env.example -- and run the deploy again; nothing else was skipped)" >&2
            exit 1
        fi
    done
    mkdir -p "$TEXTFILE_DIR" "$OBS_APPLIED"
    cd "$OBS_DIR"
    # Configs are bind mounts, so `up -d` does not notice when their contents change. Each
    # service's config tree is hashed and compared with what was applied last time; a change
    # reloads or restarts just that service. Dashboards need nothing: Grafana re-reads the
    # dashboards directory on its own. Data sources do not, hence the restart.
    obs_changed() {  # <name> <path...>  -> 0 if the content differs from the last applied
        local name=$1; shift
        local now; now=$(find "$@" -type f -print0 2>/dev/null | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | cut -c1-64)
        [[ "$(cat "$OBS_APPLIED/$name" 2>/dev/null)" == "$now" ]] && return 1
        echo "$now" > "$OBS_APPLIED/$name"
        return 0
    }
    docker compose --env-file "$OBS_ENV" up -d
    if obs_changed prometheus prometheus; then
        docker exec prometheus wget -qO- --post-data '' http://localhost:9090/-/reload >/dev/null \
            && say "observability: prometheus config reloaded"
    fi
    if obs_changed blackbox blackbox; then
        docker exec blackbox wget -qO- --post-data '' http://localhost:9115/-/reload >/dev/null 2>&1 \
            || docker compose --env-file "$OBS_ENV" restart blackbox >/dev/null
        say "observability: blackbox config reloaded"
    fi
    if obs_changed loki loki; then
        docker compose --env-file "$OBS_ENV" restart loki >/dev/null && say "observability: loki restarted for new config"
    fi
    if obs_changed alloy "$HERE/scripts/alloy"; then
        docker compose --env-file "$OBS_ENV" restart alloy >/dev/null && say "observability: alloy restarted for new config"
    fi
    if obs_changed grafana-provisioning grafana/provisioning; then
        docker compose --env-file "$OBS_ENV" restart grafana >/dev/null && say "observability: grafana restarted for new provisioning"
    fi
    docker compose --env-file "$OBS_ENV" ps --format '    {{.Name}}\t{{.Image}}\t{{.Status}}'
    cd "$STACK_DIR"
fi

# --- wifi controllers -----------------------------------------------------------------------
if [[ -d "$WIFI_DIR" ]]; then
    for var in UNIFI_MONGO_ROOT_PASSWORD UNIFI_MONGO_PASSWORD; do
        if [[ ! -f "$OBS_ENV" ]] || ! grep -qE "^$var=.+" "$OBS_ENV"; then
            echo "wifi: $var is not set in $OBS_ENV; not starting the controllers" >&2
            echo "  (add it -- see .env.example -- and run the deploy again; nothing else was skipped)" >&2
            exit 1
        fi
    done
    cd "$WIFI_DIR"
    # Nothing here is a bind-mounted config: both controllers keep their state in volumes and
    # are configured through their UIs, so `up -d` is the whole apply.
    docker compose --env-file "$OBS_ENV" up -d
    docker compose --env-file "$OBS_ENV" ps --format '    {{.Name}}\t{{.Image}}\t{{.Status}}'
    cd "$STACK_DIR"
fi
