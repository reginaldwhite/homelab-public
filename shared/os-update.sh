#!/bin/bash
# os-update.sh — apply Ubuntu package updates on this host and reboot if the kernel asks.
#
#   os-update.sh              update, upgrade, autoremove; then reboot if required (asks first)
#   os-update.sh --dry-run    show what would be upgraded and whether a reboot is pending (no sudo)
#   os-update.sh --no-reboot  upgrade but never reboot, even if one is required
#   os-update.sh --yes        do not ask before rebooting
#
# Run by a person, never by the agent or cron: it needs sudo with a password, and on
# bastion the reboot takes DNS, DHCP and the vault away for a minute or two. That is the
# whole reason this is a script a human invokes rather than a job -- unattended-upgrades
# already installs security fixes on its own and never reboots, so what accumulates is
# ordinary updates and a pending kernel, and both are decided by someone looking.
#
# Same file on every host (shared/, merged into scripts/ by deploy.sh). Order to run it
# in, by blast radius: forge, then cinema, then bastion last -- see the "Host operating
# systems" section of .claude/skills/homelab-upgrades/SKILL.md.
#
# Logs to /home/robby/logs/os-update.log, so the last run is readable after the reboot
# and the health check has something to point at.

set -uo pipefail

DRY=0; REBOOT=1; ASK=1
for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRY=1 ;;
        --no-reboot) REBOOT=0 ;;
        --yes)       ASK=0 ;;
        -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

host=$(hostname)
kernel_now=$(uname -r)
kernel_newest=$(ls /boot/vmlinuz-* 2>/dev/null | sort -V | tail -1 | sed 's|.*/vmlinuz-||')

# The dry run needs no privilege and asks for none, so the agent's health checks and a
# quick look from the laptop can use it. It reads the package lists as they are --
# unattended-upgrades refreshes them daily -- rather than running apt-get update.
if [[ $DRY -eq 1 ]]; then
    pending=$(apt list --upgradable 2>/dev/null | grep -c upgradable)
    echo "== $host: $pending package(s) upgradable (lists as last refreshed)"
    apt-get -s full-upgrade 2>/dev/null | grep -E '^(Inst|Remv)' | awk '{print "   " $1, $2, $3}'
    [[ -e /var/run/reboot-required ]] \
        && echo "== reboot already pending: $(tr '\n' ' ' < /var/run/reboot-required.pkgs 2>/dev/null)" \
        || echo "== no reboot pending"
    echo "== kernel running $kernel_now, newest installed $kernel_newest"
    exit 0
fi

# Re-run under sudo, so `os-update.sh` from a normal shell prompts once for the password.
if [[ $EUID -ne 0 ]]; then
    exec sudo -- "$0" "$@"
fi

LOG="/home/robby/logs/os-update.log"
OWNER="${SUDO_USER:-robby}"
mkdir -p "$(dirname "$LOG")"
touch "$LOG" && chown "$OWNER":"$OWNER" "$LOG" 2>/dev/null
log() { echo "$(date -Iseconds) [os-update] $*" | tee -a "$LOG"; }

export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
apt-get update -qq 2>&1 | tail -3
pending=$(apt list --upgradable 2>/dev/null | grep -c upgradable)
log "$host: starting, $pending package(s) upgradable, kernel $kernel_now"

# apt's own output goes to the log in full; the terminal gets a count. The exit status
# is read from apt directly rather than from the end of a pipeline, where grep finding
# nothing (a host with nothing to upgrade) would look like a failure.
tmp=$(mktemp)
apt-get -y full-upgrade >"$tmp" 2>&1; rc=$?
cat "$tmp" >> "$LOG"
echo "   $(grep -cE '^(Setting up|Unpacking|Removing) ' "$tmp") package operations"
rm -f "$tmp"
if [[ $rc -ne 0 ]]; then
    log "ERROR: full-upgrade exited $rc; see $LOG"
    exit 1
fi
apt-get -y autoremove >>"$LOG" 2>&1 || log "WARNING: autoremove exited non-zero; see $LOG"

left=$(apt list --upgradable 2>/dev/null | grep -c upgradable)
log "$host: upgrade done, $left package(s) still upgradable (phased or held), kernel newest $(ls /boot/vmlinuz-* | sort -V | tail -1 | sed 's|.*/vmlinuz-||')"

if [[ ! -e /var/run/reboot-required ]]; then
    log "$host: no reboot required"
    exit 0
fi
log "$host: reboot required for: $(tr '\n' ' ' < /var/run/reboot-required.pkgs 2>/dev/null)"

if [[ $REBOOT -eq 0 ]]; then
    log "$host: --no-reboot given, leaving it pending"
    exit 0
fi

# bastion is the one host whose reboot everyone notices. Say so before asking.
if [[ "$host" == "file" ]]; then
    echo
    echo "   bastion runs DNS, DHCP and the vault for the whole house."
    echo "   A reboot takes them away for one to two minutes; devices keep their"
    echo "   leases, but nothing resolves until it is back."
    echo
fi

if [[ $ASK -eq 1 ]]; then
    if [[ ! -t 0 ]]; then
        log "$host: reboot needed but no terminal to ask on; rerun with --yes to reboot"
        exit 0
    fi
    read -r -p "   Reboot $host now? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || { log "$host: reboot declined, still pending"; exit 0; }
fi

log "$host: rebooting"
sleep 2
systemctl reboot
