#!/bin/bash
# wake-vault.sh — wake vault, and wait until it can actually receive a backup.
#
# This replaces the smart plug as vault's power-on mechanism. What had to be true
# before Wake-on-LAN worked at all is in docs/vault-power.md; the short version is
# that arming the NIC was not enough on this board — the wake also has to be enabled
# on the NIC's PCIe root port, and without that everything looks correctly
# configured and nothing ever happens.
#
# Three gates, not one, because "answers ping" is not "ready for a backup":
#
#   1. ping   — the board powered on
#   2. ssh    — the OS is up
#   3. array  — /mnt/user is actually mounted
#
# Gate 3 is the one that matters most and is easiest to skip. Unraid's root
# filesystem is RAM, and `rsync --mkpath` would cheerfully create
# /mnt/user/backups on it and report success — a backup that reports success and
# evaporates at the next boot. That is worse than a failed backup, so it is checked
# rather than assumed.
#
# Both MACs are targeted: the NICs are bonded active-backup, so either could be
# carrying the link, and the address the bond presents is not the same as the
# cabled interface's permanent one.

VAULT_IP="192.168.0.212"
VAULT_SSH="root@$VAULT_IP"
SSH_KEY="/home/robby/.ssh/id_ed25519"
# eth0 permanent (= the address the bond presents) and eth1 permanent.
VAULT_MACS=("e0:cb:4e:9d:02:6a" "e0:cb:4e:80:92:3d")

PING_TIMEOUT=300    # POST on this 2011 board is slow; observed ~60s to first ping
SSH_TIMEOUT=180
ARRAY_TIMEOUT=300
LOG="/home/robby/logs/power.log"

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Iseconds) [wake-vault] $*" >> "$LOG"; }

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no
          -o UserKnownHostsFile=/dev/null -i "$SSH_KEY")

vault_pings() { ping -c1 -W2 "$VAULT_IP" &>/dev/null; }
vault_ssh()   { ssh "${SSH_OPTS[@]}" "$VAULT_SSH" true 2>/dev/null; }
array_up() {
    [[ "$(ssh "${SSH_OPTS[@]}" "$VAULT_SSH" \
        "grep -E '^mdState=' /var/local/emhttp/var.ini 2>/dev/null | cut -d'\"' -f2" \
        2>/dev/null)" == "STARTED" ]]
}

send_wol() {
    local mac
    for mac in "${VAULT_MACS[@]}"; do
        if command -v wakeonlan &>/dev/null; then
            wakeonlan "$mac" >/dev/null && continue
        fi
        python3 - "$mac" <<'PY' >/dev/null || return 1
import socket, sys
mac = sys.argv[1].replace(":", "")
packet = bytes.fromhex("ff" * 6 + mac * 16)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for dest in ("192.168.0.255", "255.255.255.255"):
    s.sendto(packet, (dest, 9))
PY
    done
}

if vault_pings; then
    log "vault already up, nothing to do"
else
    if ! send_wol; then
        log "ERROR: could not send the magic packet"
        exit 1
    fi
    log "WoL sent to ${VAULT_MACS[*]}, waiting for vault to boot..."
    SECONDS=0
    while (( SECONDS < PING_TIMEOUT )); do
        vault_pings && break
        sleep 5
    done
    if ! vault_pings; then
        log "ERROR: vault did not answer ping within ${PING_TIMEOUT}s of WoL"
        exit 1
    fi
    log "vault answers ping (${SECONDS}s)"
fi

SECONDS=0
while (( SECONDS < SSH_TIMEOUT )); do
    vault_ssh && break
    sleep 5
done
if ! vault_ssh; then
    log "ERROR: vault answers ping but not SSH after ${SSH_TIMEOUT}s"
    exit 1
fi

SECONDS=0
while (( SECONDS < ARRAY_TIMEOUT )); do
    array_up && break
    sleep 10
done
if ! array_up; then
    log "ERROR: vault is up but the array never started — /mnt/user is not mounted, so a backup now would write to its RAM filesystem and be lost"
    exit 1
fi

log "vault is ready: ssh up and array STARTED"
