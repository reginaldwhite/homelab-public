#!/bin/bash
# vault-common.sh — how bastion talks to vault, and the gate that must hold before
# anything writes to it. Sourced by the vault scripts; never run on its own.
#
# Four scripts on bastion touch vault — wake, sleep, backup, sync — and three of
# them need the same answer to the same question before they do anything: is the
# array actually mounted?
#
# That check existed, but only inside wake-vault.sh, and cron runs every job
# independently. wake-vault.sh exiting 1 at 2:00 did nothing to stop
# backup-to-tower.sh at 2:30 or sync-forge.sh at 3:00, neither of which looked.
# With the array stopped, Unraid's root filesystem is RAM and `rsync --mkpath`
# will cheerfully create /mnt/user/backups there, transfer everything, exit 0, and
# lose the lot at the next boot. The logs would read "Weekly backup complete".
#
# That is worse than a failed backup, because it looks like a good one — which is
# the exact reasoning already written into wake-vault.sh. It belongs everywhere
# that writes, so it lives here, once.

VAULT_IP="192.168.0.212"
VAULT_SSH="root@$VAULT_IP"
VAULT_SSH_KEY="/home/robby/.ssh/id_ed25519"

# accept-new, not no/dev/null: this authenticates as root on the machine holding
# every backup, so an unexplained host key change should stop the job rather than
# be accepted silently. After a legitimate rebuild: ssh-keygen -R 192.168.0.212
VAULT_SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10
                -o StrictHostKeyChecking=accept-new -i "$VAULT_SSH_KEY")

# For rsync -e, which wants one string rather than an array.
VAULT_RSYNC_SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i $VAULT_SSH_KEY"

vault_pings() { ping -c1 -W2 "$VAULT_IP" &>/dev/null; }

vault_ssh() { ssh "${VAULT_SSH_OPTS[@]}" "$VAULT_SSH" "$@"; }

# True only when the array is started, so /mnt/user is a real filesystem.
vault_array_started() {
    local state
    state=$(vault_ssh "grep -E '^mdState=' /var/local/emhttp/var.ini 2>/dev/null | cut -d'\"' -f2" 2>/dev/null)
    [[ "$state" == "STARTED" ]]
}

# The gate itself. Callers pass their log tag and log file; it refuses rather than
# writing somewhere that will not survive. Returns 1 so the caller can abort.
vault_require_array() {
    local tag=$1 logfile=$2
    if vault_array_started; then
        return 0
    fi
    echo "$(date -Iseconds) [$tag] ERROR: vault's array is not started, so /mnt/user is its RAM filesystem — refusing to back up, because it would report success and be gone at the next boot" >> "$logfile"
    return 1
}
